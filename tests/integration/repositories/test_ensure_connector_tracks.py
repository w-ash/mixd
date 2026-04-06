"""Integration tests for ensure_connector_tracks.

Tests the new ConnectorRepositoryProtocol method that bulk-upserts
connector_tracks rows and returns a (connector_name, external_id) → UUID map.
Covers round-trip persistence, idempotency, and FK compatibility with MatchReview.
"""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.match_review import MatchReview
from src.infrastructure.persistence.database.db_models import DBTrack
from src.infrastructure.persistence.repositories.match_review import (
    MatchReviewRepository,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)


async def _seed_track(session: AsyncSession) -> UUID:
    """Insert a canonical track, return its ID."""
    uid = uuid4().hex[:8]
    track = DBTrack(
        title=f"Track {uid}",
        artists=[{"name": f"Artist {uid}"}],
        spotify_id=f"sp_{uid}",
    )
    session.add(track)
    await session.flush()
    return track.id


class TestEnsureConnectorTracks:
    """Round-trip and idempotency tests for ensure_connector_tracks."""

    async def test_creates_rows_and_returns_uuid_map(self, db_session: AsyncSession):
        """Upserting new connector tracks returns valid UUID map."""
        repo = TrackConnectorRepository(db_session)

        tracks_data = [
            {
                "connector_id": "sp_abc123",
                "title": "Song A",
                "artists": ["Artist One"],
                "album": "Album X",
                "duration_ms": 210000,
                "isrc": "USRC10000001",
            },
            {
                "connector_id": "sp_def456",
                "title": "Song B",
                "artists": ["Artist Two", "Artist Three"],
            },
        ]

        result = await repo.ensure_connector_tracks("spotify", tracks_data)

        assert len(result) == 2
        assert ("spotify", "sp_abc123") in result
        assert ("spotify", "sp_def456") in result

        # UUIDs are distinct
        ids = list(result.values())
        assert ids[0] != ids[1]

    async def test_idempotent_on_rerun(self, db_session: AsyncSession):
        """Calling twice with same data returns same UUIDs, no duplicates."""
        repo = TrackConnectorRepository(db_session)

        tracks_data = [
            {
                "connector_id": "sp_idem_001",
                "title": "Idempotent Song",
                "artists": ["Idem Artist"],
            },
        ]

        first = await repo.ensure_connector_tracks("spotify", tracks_data)
        second = await repo.ensure_connector_tracks("spotify", tracks_data)

        assert first == second

    async def test_empty_input_returns_empty_map(self, db_session: AsyncSession):
        """Empty tracks_data returns empty dict without DB calls."""
        repo = TrackConnectorRepository(db_session)
        result = await repo.ensure_connector_tracks("spotify", [])
        assert result == {}

    async def test_returned_uuids_usable_as_match_review_fk(
        self, db_session: AsyncSession
    ):
        """UUIDs from ensure_connector_tracks can be used as MatchReview.connector_track_id."""
        track_id = await _seed_track(db_session)
        connector_repo = TrackConnectorRepository(db_session)
        review_repo = MatchReviewRepository(db_session)

        # Create connector_tracks via ensure_connector_tracks
        ct_map = await connector_repo.ensure_connector_tracks(
            "spotify",
            [
                {
                    "connector_id": "sp_review_test",
                    "title": "Review Song",
                    "artists": ["RA"],
                }
            ],
        )
        ct_id = ct_map["spotify", "sp_review_test"]

        # Use the UUID as FK in a MatchReview
        review = MatchReview(
            track_id=track_id,
            connector_name="spotify",
            connector_track_id=ct_id,
            match_method="artist_title",
            confidence=60,
            match_weight=2.5,
        )
        created = await review_repo.create_review(review)

        assert created.connector_track_id == ct_id
        assert created.status == "pending"
