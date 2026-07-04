"""Integration tests for data integrity monitoring repository queries.

Tests real SQL queries against SQLite for primary mapping violations,
orphaned connector tracks, duplicate tracks, and stale pending reviews.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import ReviewStatus
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBMatchReview,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.match_review import (
    MatchReviewRepository,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)
from src.infrastructure.persistence.repositories.track.core import TrackRepository


async def _seed_track(session: AsyncSession, title: str = "Track") -> int:
    """Insert a track and return its ID."""
    uid = uuid4().hex[:8]
    track = DBTrack(
        title=f"{title} {uid}",
        artists={"names": [f"Artist {uid}"]},
        spotify_id=f"sp_{uid}",
    )
    session.add(track)
    await session.flush()
    return track.id


async def _seed_connector_track(
    session: AsyncSession, connector_name: str = "spotify"
) -> int:
    """Insert a connector track and return its ID."""
    uid = uuid4().hex[:8]
    ct = DBConnectorTrack(
        connector_name=connector_name,
        connector_track_identifier=f"ct_{uid}",
        title=f"CT {uid}",
        artists={"names": [f"CT Artist {uid}"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    session.add(ct)
    await session.flush()
    return ct.id


async def _seed_mapping(
    session: AsyncSession,
    track_id: int,
    ct_id: int,
    connector_name: str = "spotify",
    is_primary: bool = True,
) -> int:
    """Insert a track mapping and return its ID."""
    mapping = DBTrackMapping(
        track_id=track_id,
        connector_track_id=ct_id,
        connector_name=connector_name,
        match_method="direct",
        confidence=100,
        origin="automatic",
        is_primary=is_primary,
    )
    session.add(mapping)
    await session.flush()
    return mapping.id


class TestMultiplePrimaryViolations:
    async def test_no_violations_on_clean_db(self, db_session: AsyncSession):
        repo = TrackConnectorRepository(db_session)
        result = await repo.find_multiple_primary_violations()
        assert result == []

    async def test_no_violation_when_primaries_on_different_connectors(
        self, db_session: AsyncSession
    ):
        """A track with primary mappings on two different connectors is normal.

        Multiple primaries for the SAME connector is the violation. The partial
        unique index prevents this at the DB level, so we test the detection
        query logic by creating primaries on different connectors and verifying
        the query correctly counts per (track, connector) pair.
        """
        track_id = await _seed_track(db_session)
        ct1_id = await _seed_connector_track(db_session, connector_name="spotify")
        ct2_id = await _seed_connector_track(db_session, connector_name="lastfm")

        # One primary per connector — this is normal
        await _seed_mapping(
            db_session, track_id, ct1_id, connector_name="spotify", is_primary=True
        )
        await _seed_mapping(
            db_session, track_id, ct2_id, connector_name="lastfm", is_primary=True
        )

        repo = TrackConnectorRepository(db_session)
        result = await repo.find_multiple_primary_violations()
        # No violations — one primary per connector is correct
        assert result == []

    async def test_ignores_single_primary(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct1_id = await _seed_connector_track(db_session)
        ct2_id = await _seed_connector_track(db_session)

        await _seed_mapping(db_session, track_id, ct1_id, is_primary=True)
        await _seed_mapping(
            db_session, track_id, ct2_id, connector_name="lastfm", is_primary=False
        )

        repo = TrackConnectorRepository(db_session)
        result = await repo.find_multiple_primary_violations()
        assert result == []


class TestMissingPrimaryViolations:
    async def test_no_violations_on_clean_db(self, db_session: AsyncSession):
        repo = TrackConnectorRepository(db_session)
        result = await repo.find_missing_primary_violations()
        assert result == []

    async def test_detects_missing_primary(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)

        await _seed_mapping(db_session, track_id, ct_id, is_primary=False)

        repo = TrackConnectorRepository(db_session)
        result = await repo.find_missing_primary_violations()
        assert len(result) == 1
        assert result[0]["track_id"] == track_id

    async def test_ignores_tracks_with_primary(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)

        await _seed_mapping(db_session, track_id, ct_id, is_primary=True)

        repo = TrackConnectorRepository(db_session)
        result = await repo.find_missing_primary_violations()
        assert result == []


class TestOrphanedConnectorTracks:
    async def test_no_orphans_on_clean_db(self, db_session: AsyncSession):
        repo = TrackConnectorRepository(db_session)
        count = await repo.count_orphaned_connector_tracks()
        assert count == 0

    async def test_detects_orphan(self, db_session: AsyncSession):
        # Connector track with no mapping pointing to it
        await _seed_connector_track(db_session)

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_orphaned_connector_tracks()
        assert count == 1

    async def test_ignores_mapped_connector_tracks(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)
        await _seed_mapping(db_session, track_id, ct_id)

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_orphaned_connector_tracks()
        assert count == 0


class TestDuplicateTracksByFingerprint:
    async def test_no_duplicates_on_clean_db(self, db_session: AsyncSession):
        repo = TrackRepository(db_session)
        result = await repo.find_duplicate_tracks_by_fingerprint(user_id="default")
        assert result == []

    async def test_detects_duplicate_title_artist_album(self, db_session: AsyncSession):
        now = datetime.now(UTC)
        for _ in range(2):
            track = DBTrack(
                title="Same Song",
                artists={"names": ["Same Artist"]},
                album="Same Album",
            )
            db_session.add(track)
        await db_session.flush()

        repo = TrackRepository(db_session)
        result = await repo.find_duplicate_tracks_by_fingerprint(user_id="default")
        assert len(result) == 1
        assert result[0]["title"] == "Same Song"
        assert result[0]["count"] == 2
        assert len(result[0]["track_ids"]) == 2

    async def test_ignores_different_albums(self, db_session: AsyncSession):
        for album in ["Album A", "Album B"]:
            track = DBTrack(
                title="Same Song",
                artists={"names": ["Same Artist"]},
                album=album,
            )
            db_session.add(track)
        await db_session.flush()

        repo = TrackRepository(db_session)
        result = await repo.find_duplicate_tracks_by_fingerprint(user_id="default")
        assert result == []


class TestStalePendingReviews:
    async def test_no_stale_on_clean_db(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)
        count = await repo.count_stale_pending(user_id="default", older_than_days=30)
        assert count == 0

    async def test_detects_stale_review(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)

        old_date = datetime.now(UTC) - timedelta(days=60)
        review = DBMatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=65,
            match_weight=3.5,
            status=ReviewStatus.PENDING,
            created_at=old_date,
            updated_at=old_date,
        )
        db_session.add(review)
        await db_session.flush()

        repo = MatchReviewRepository(db_session)
        count = await repo.count_stale_pending(user_id="default", older_than_days=30)
        assert count == 1

    async def test_ignores_recent_reviews(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)

        review = DBMatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=65,
            match_weight=3.5,
            status=ReviewStatus.PENDING,
        )
        db_session.add(review)
        await db_session.flush()

        repo = MatchReviewRepository(db_session)
        count = await repo.count_stale_pending(user_id="default", older_than_days=30)
        assert count == 0


class TestCountStaleDenormalizedIds:
    """Stale/dangling denormalized spotify_id detection (Q7 mirror)."""

    async def test_no_drift_on_clean_db(self, db_session: AsyncSession):
        repo = TrackConnectorRepository(db_session)
        count = await repo.count_stale_denormalized_ids(user_id="default")
        assert count == 0

    async def test_ignores_track_with_no_spotify_id_and_no_mapping(
        self, db_session: AsyncSession
    ):
        track = DBTrack(title="Track", artists={"names": ["Artist"]})
        db_session.add(track)
        await db_session.flush()

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_stale_denormalized_ids(user_id="default")
        assert count == 0

    async def test_ignores_agreeing_primary_mapping(self, db_session: AsyncSession):
        uid = uuid4().hex[:8]
        matching_id = f"sp_{uid}"
        track = DBTrack(
            title="Track", artists={"names": ["Artist"]}, spotify_id=matching_id
        )
        db_session.add(track)
        ct = DBConnectorTrack(
            connector_name="spotify",
            connector_track_identifier=matching_id,
            title="CT",
            artists={"names": ["Artist"]},
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        db_session.add(ct)
        await db_session.flush()
        await _seed_mapping(db_session, track.id, ct.id, is_primary=True)
        await db_session.flush()

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_stale_denormalized_ids(user_id="default")
        assert count == 0

    async def test_detects_column_disagrees_with_primary(
        self, db_session: AsyncSession
    ):
        track_id = await _seed_track(db_session)  # spotify_id = "sp_<uid>"
        ct_id = await _seed_connector_track(db_session)  # identifier = "ct_<uid>"
        await _seed_mapping(db_session, track_id, ct_id, is_primary=True)

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_stale_denormalized_ids(user_id="default")
        assert count == 1

    async def test_detects_column_set_but_no_mapping(self, db_session: AsyncSession):
        await _seed_track(db_session)  # spotify_id set, no mapping at all

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_stale_denormalized_ids(user_id="default")
        assert count == 1

    async def test_scoped_to_user(self, db_session: AsyncSession):
        uid = uuid4().hex[:8]
        track = DBTrack(
            title="Other User Track",
            artists={"names": ["Artist"]},
            spotify_id=f"sp_{uid}",
            user_id="other-user",
        )
        db_session.add(track)
        await db_session.flush()

        repo = TrackConnectorRepository(db_session)
        assert await repo.count_stale_denormalized_ids(user_id="default") == 0
        assert await repo.count_stale_denormalized_ids(user_id="other-user") == 1


class TestCountConfidenceEvidenceDivergence:
    """Bumped-confidence detection: confidence=100 but evidence disagrees (Q6 mirror)."""

    async def test_no_divergence_on_clean_db(self, db_session: AsyncSession):
        repo = TrackConnectorRepository(db_session)
        count = await repo.count_confidence_evidence_divergence(user_id="default")
        assert count == 0

    async def test_ignores_null_evidence(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)
        mapping = DBTrackMapping(
            track_id=track_id,
            connector_track_id=ct_id,
            connector_name="spotify",
            match_method="direct_import",
            confidence=100,
            origin="automatic",
            is_primary=True,
        )
        db_session.add(mapping)
        await db_session.flush()

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_confidence_evidence_divergence(user_id="default")
        assert count == 0

    async def test_ignores_agreeing_evidence(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)
        mapping = DBTrackMapping(
            track_id=track_id,
            connector_track_id=ct_id,
            connector_name="spotify",
            match_method="artist_title",
            confidence=100,
            confidence_evidence={"final_score": 100},
            origin="automatic",
            is_primary=True,
        )
        db_session.add(mapping)
        await db_session.flush()

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_confidence_evidence_divergence(user_id="default")
        assert count == 0

    async def test_detects_bumped_confidence(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)
        mapping = DBTrackMapping(
            track_id=track_id,
            connector_track_id=ct_id,
            connector_name="spotify",
            match_method="artist_title",
            confidence=100,
            confidence_evidence={"final_score": 82.5},
            origin="automatic",
            is_primary=True,
        )
        db_session.add(mapping)
        await db_session.flush()

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_confidence_evidence_divergence(user_id="default")
        assert count == 1

    async def test_ignores_lower_confidence_even_with_divergent_evidence(
        self, db_session: AsyncSession
    ):
        """Only confidence=100 rows are in scope — a lower confidence is not 'bumped'."""
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)
        mapping = DBTrackMapping(
            track_id=track_id,
            connector_track_id=ct_id,
            connector_name="spotify",
            match_method="artist_title",
            confidence=90,
            confidence_evidence={"final_score": 82.5},
            origin="automatic",
            is_primary=True,
        )
        db_session.add(mapping)
        await db_session.flush()

        repo = TrackConnectorRepository(db_session)
        count = await repo.count_confidence_evidence_divergence(user_id="default")
        assert count == 0


class TestCountCreatedSince:
    """Review-inflow counting: reviews created within the last N days, any status."""

    async def test_no_reviews_on_clean_db(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)
        count = await repo.count_created_since(7, user_id="default")
        assert count == 0

    async def test_counts_recent_regardless_of_status(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)
        review = DBMatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=65,
            match_weight=3.5,
            status=ReviewStatus.ACCEPTED,
        )
        db_session.add(review)
        await db_session.flush()

        repo = MatchReviewRepository(db_session)
        count = await repo.count_created_since(7, user_id="default")
        assert count == 1

    async def test_ignores_older_than_window(self, db_session: AsyncSession):
        track_id = await _seed_track(db_session)
        ct_id = await _seed_connector_track(db_session)
        old_date = datetime.now(UTC) - timedelta(days=45)
        review = DBMatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=65,
            match_weight=3.5,
            status=ReviewStatus.PENDING,
            created_at=old_date,
            updated_at=old_date,
        )
        db_session.add(review)
        await db_session.flush()

        repo = MatchReviewRepository(db_session)
        assert await repo.count_created_since(30, user_id="default") == 0
        assert await repo.count_created_since(60, user_id="default") == 1


class TestCountPendingByMethod:
    """Pending-review counts grouped by match_method (e.g., isrc_suspect depth)."""

    async def test_no_reviews_on_clean_db(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)
        result = await repo.count_pending_by_method(user_id="default")
        assert result == {}

    async def test_groups_pending_by_method_excludes_resolved(
        self, db_session: AsyncSession
    ):
        track_id, ct_id = (
            await _seed_track(db_session),
            await _seed_connector_track(db_session),
        )
        track_id2, ct_id2 = (
            await _seed_track(db_session),
            await _seed_connector_track(db_session),
        )
        track_id3, ct_id3 = (
            await _seed_track(db_session),
            await _seed_connector_track(db_session),
        )

        db_session.add_all([
            DBMatchReview(
                track_id=track_id,
                connector_name="spotify",
                connector_track_id=ct_id,
                match_method="isrc_suspect",
                confidence=70,
                match_weight=3.0,
                status=ReviewStatus.PENDING,
            ),
            DBMatchReview(
                track_id=track_id2,
                connector_name="spotify",
                connector_track_id=ct_id2,
                match_method="isrc_suspect",
                confidence=72,
                match_weight=3.0,
                status=ReviewStatus.PENDING,
            ),
            DBMatchReview(
                track_id=track_id3,
                connector_name="spotify",
                connector_track_id=ct_id3,
                match_method="artist_title",
                confidence=65,
                match_weight=3.0,
                status=ReviewStatus.ACCEPTED,
            ),
        ])
        await db_session.flush()

        repo = MatchReviewRepository(db_session)
        result = await repo.count_pending_by_method(user_id="default")
        assert result == {"isrc_suspect": 2}
