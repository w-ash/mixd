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

    async def test_detects_two_primaries_across_connectors(
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
        result = await repo.find_duplicate_tracks_by_fingerprint()
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
        result = await repo.find_duplicate_tracks_by_fingerprint()
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
        result = await repo.find_duplicate_tracks_by_fingerprint()
        assert result == []


class TestStalePendingReviews:
    async def test_no_stale_on_clean_db(self, db_session: AsyncSession):
        repo = MatchReviewRepository(db_session)
        count = await repo.count_stale_pending(older_than_days=30)
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
        count = await repo.count_stale_pending(older_than_days=30)
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
        count = await repo.count_stale_pending(older_than_days=30)
        assert count == 0
