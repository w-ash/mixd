"""Integration tests for ConnectorTrackRepository.get_match_method_stats().

Tests the SQL aggregation query against a real SQLite database, verifying
correct grouping, counting, confidence aggregation, and recent-window filtering.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


def _make_db_track() -> DBTrack:
    """Create a unique DBTrack for test isolation."""
    uid = str(uuid4())[:8]
    track = DBTrack(
        title=f"Track_{uid}",
        artists={"names": [f"Artist_{uid}"]},
        duration_ms=200000,
    )
    track.mappings = []
    track.likes = []
    track.plays = []
    return track


def _make_connector_track(connector: str) -> DBConnectorTrack:
    """Create a unique DBConnectorTrack."""
    uid = str(uuid4())[:8]
    ct = DBConnectorTrack(
        connector_name=connector,
        connector_track_identifier=f"{connector}_{uid}",
        title=f"CT_{uid}",
        artists={"names": [f"Artist_{uid}"]},
        raw_metadata={},
    )
    ct.mappings = []
    return ct


async def _insert_mapping(
    session,
    track: DBTrack,
    connector_track: DBConnectorTrack,
    match_method: str,
    confidence: int,
    created_at: datetime | None = None,
) -> DBTrackMapping:
    """Insert a track + connector_track + mapping into the database."""
    # Persist track and connector track if not yet persisted
    if track.id is None:
        session.add(track)
        await session.flush()
    if connector_track.id is None:
        session.add(connector_track)
        await session.flush()

    mapping = DBTrackMapping(
        track_id=track.id,
        connector_track_id=connector_track.id,
        connector_name=connector_track.connector_name,
        match_method=match_method,
        confidence=confidence,
        is_primary=True,
        origin="automatic",
    )
    session.add(mapping)
    await session.flush()

    # Override created_at if specified (for testing recent_count filtering)
    if created_at is not None:
        mapping.created_at = created_at
        await session.flush()

    return mapping


class TestMatchMethodStatsEmpty:
    """No mappings in database returns empty list."""

    async def test_empty_returns_empty_list(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_repository()

        result = await repo.get_match_method_stats()

        assert result == []


class TestMatchMethodStatsAggregation:
    """Verify correct grouping and counting by match_method + connector_name."""

    async def test_groups_by_method_and_connector(self, db_session):
        # Insert 2 direct_import/spotify + 1 artist_title/lastfm
        t1, t2, t3 = _make_db_track(), _make_db_track(), _make_db_track()
        ct1 = _make_connector_track("spotify")
        ct2 = _make_connector_track("spotify")
        ct3 = _make_connector_track("lastfm")

        await _insert_mapping(db_session, t1, ct1, "direct_import", 100)
        await _insert_mapping(db_session, t2, ct2, "direct_import", 100)
        await _insert_mapping(db_session, t3, ct3, "artist_title", 90)
        await db_session.commit()

        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_repository()
        result = await repo.get_match_method_stats()

        # Should have 2 groups
        assert len(result) == 2

        # Ordered by total_count desc: direct_import first
        assert result[0]["match_method"] == "direct_import"
        assert result[0]["connector_name"] == "spotify"
        assert result[0]["total_count"] == 2

        assert result[1]["match_method"] == "artist_title"
        assert result[1]["connector_name"] == "lastfm"
        assert result[1]["total_count"] == 1


class TestMatchMethodStatsRecentWindow:
    """Verify recent_count filters by created_at within the window."""

    async def test_recent_count_filters_by_date(self, db_session):
        now = datetime.now(UTC)
        old_date = now - timedelta(days=60)

        t1, t2, t3 = _make_db_track(), _make_db_track(), _make_db_track()
        ct1 = _make_connector_track("spotify")
        ct2 = _make_connector_track("spotify")
        ct3 = _make_connector_track("spotify")

        # 2 recent, 1 old — all same method/connector
        await _insert_mapping(db_session, t1, ct1, "direct_import", 100, created_at=now)
        await _insert_mapping(
            db_session, t2, ct2, "direct_import", 100, created_at=now - timedelta(days=5)
        )
        await _insert_mapping(
            db_session, t3, ct3, "direct_import", 100, created_at=old_date
        )
        await db_session.commit()

        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_repository()
        result = await repo.get_match_method_stats(recent_days=30)

        assert len(result) == 1
        assert result[0]["total_count"] == 3
        assert result[0]["recent_count"] == 2


class TestMatchMethodStatsConfidence:
    """Verify avg/min/max confidence calculations."""

    async def test_confidence_aggregation(self, db_session):
        t1, t2, t3 = _make_db_track(), _make_db_track(), _make_db_track()
        ct1 = _make_connector_track("spotify")
        ct2 = _make_connector_track("spotify")
        ct3 = _make_connector_track("spotify")

        await _insert_mapping(db_session, t1, ct1, "isrc_match", 95)
        await _insert_mapping(db_session, t2, ct2, "isrc_match", 85)
        await _insert_mapping(db_session, t3, ct3, "isrc_match", 100)
        await db_session.commit()

        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_repository()
        result = await repo.get_match_method_stats()

        assert len(result) == 1
        row = result[0]
        assert row["min_confidence"] == 85
        assert row["max_confidence"] == 100
        # avg of 95, 85, 100 = 93.333... → rounded to 93.3
        assert row["avg_confidence"] == 93.3
