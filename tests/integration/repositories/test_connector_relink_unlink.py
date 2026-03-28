"""Integration tests for connector repository relink/unlink methods.

Tests new repository methods against a real database session:
get_mapping_by_id, delete_mapping, update_mapping_track,
count_mappings_for_connector_track, get_remaining_mappings,
ensure_primary_for_connector, get_connector_track_by_id.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)


@pytest.fixture
def connector_repo(db_session: AsyncSession) -> TrackConnectorRepository:
    """Create a TrackConnectorRepository with the test session."""
    return TrackConnectorRepository(db_session)


async def _setup_track_with_mapping(
    db_session: AsyncSession,
    *,
    connector_name: str = "spotify",
    external_id: str = "spotify:abc123",
    is_primary: bool = True,
    confidence: int = 95,
    origin: str = "automatic",
) -> tuple[UUID, UUID, UUID]:
    """Create a track, connector track, and mapping. Returns (track_id, ct_id, mapping_id)."""
    import uuid

    uid = str(uuid.uuid4())[:8]

    db_track = DBTrack(
        title=f"Track {uid}",
        artists={"names": [f"Artist {uid}"]},
        spotify_id=external_id if connector_name == "spotify" else None,
    )
    db_session.add(db_track)
    await db_session.flush()

    db_ct = DBConnectorTrack(
        connector_name=connector_name,
        connector_track_identifier=external_id,
        title=f"CT {uid}",
        artists={"names": [f"Artist {uid}"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(db_ct)
    await db_session.flush()

    db_mapping = DBTrackMapping(
        track_id=db_track.id,
        connector_track_id=db_ct.id,
        connector_name=connector_name,
        match_method="isrc",
        confidence=confidence,
        origin=origin,
        is_primary=is_primary,
    )
    db_session.add(db_mapping)
    await db_session.flush()

    return db_track.id, db_ct.id, db_mapping.id


class TestGetMappingById:
    """get_mapping_by_id returns domain entity or None."""

    async def test_returns_mapping_when_exists(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        _, _, mapping_id = await _setup_track_with_mapping(db_session)

        result = await connector_repo.get_mapping_by_id(mapping_id)

        assert result is not None
        assert result.id == mapping_id
        assert result.connector_name == "spotify"

    async def test_returns_none_when_missing(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        result = await connector_repo.get_mapping_by_id(uuid7())
        assert result is None


class TestDeleteMapping:
    """delete_mapping removes the row and returns pre-deletion entity."""

    async def test_deletes_and_returns_entity(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        _, _, mapping_id = await _setup_track_with_mapping(db_session)

        result = await connector_repo.delete_mapping(mapping_id)

        assert result.id == mapping_id
        # Verify it's actually deleted
        lookup = await connector_repo.get_mapping_by_id(mapping_id)
        assert lookup is None

    async def test_raises_not_found_for_missing(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        with pytest.raises(NotFoundError):
            await connector_repo.delete_mapping(uuid7())


class TestUpdateMappingTrack:
    """update_mapping_track moves a mapping to a different track."""

    async def test_moves_mapping_to_new_track(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        track_id, _, mapping_id = await _setup_track_with_mapping(db_session)

        # Create target track
        target = DBTrack(title="Target", artists={"names": ["Target Artist"]})
        db_session.add(target)
        await db_session.flush()

        result = await connector_repo.update_mapping_track(
            mapping_id, target.id, "manual_override"
        )

        assert result.track_id == target.id
        assert result.origin == "manual_override"
        assert result.is_primary is False

    async def test_raises_not_found_for_missing(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        with pytest.raises(NotFoundError):
            await connector_repo.update_mapping_track(
                uuid7(), uuid7(), "manual_override"
            )


class TestCountMappingsForConnectorTrack:
    """count_mappings_for_connector_track returns the mapping count."""

    async def test_counts_existing_mappings(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        _, ct_id, _ = await _setup_track_with_mapping(db_session)

        count = await connector_repo.count_mappings_for_connector_track(ct_id)

        assert count == 1

    async def test_zero_when_no_mappings(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        count = await connector_repo.count_mappings_for_connector_track(uuid7())
        assert count == 0


class TestGetRemainingMappings:
    """get_remaining_mappings returns mappings ordered by confidence desc."""

    async def test_returns_ordered_by_confidence(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        import uuid

        uid = str(uuid.uuid4())[:8]

        # Create track with two mappings at different confidences
        db_track = DBTrack(title=f"Multi {uid}", artists={"names": ["Multi Artist"]})
        db_session.add(db_track)
        await db_session.flush()

        for i, (ext_id, conf) in enumerate([
            (f"sp:{uid}:low", 50),
            (f"sp:{uid}:high", 95),
        ]):
            ct = DBConnectorTrack(
                connector_name="spotify",
                connector_track_identifier=ext_id,
                title=f"CT {i}",
                artists={"names": ["A"]},
                raw_metadata={},
                last_updated=datetime.now(UTC),
            )
            db_session.add(ct)
            await db_session.flush()
            m = DBTrackMapping(
                track_id=db_track.id,
                connector_track_id=ct.id,
                connector_name="spotify",
                match_method="search",
                confidence=conf,
                is_primary=False,
            )
            db_session.add(m)
        await db_session.flush()

        result = await connector_repo.get_remaining_mappings(db_track.id, "spotify")

        assert len(result) == 2
        assert result[0].confidence == 95
        assert result[1].confidence == 50


class TestEnsurePrimaryForConnector:
    """ensure_primary_for_connector promotes or clears primary mapping."""

    async def test_promotes_highest_confidence_when_no_primary(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        import uuid

        uid = str(uuid.uuid4())[:8]

        db_track = DBTrack(title=f"NoPri {uid}", artists={"names": ["A"]})
        db_session.add(db_track)
        await db_session.flush()

        # Two non-primary mappings
        for ext_id, conf in [
            (f"sp:{uid}:a", 60),
            (f"sp:{uid}:b", 90),
        ]:
            ct = DBConnectorTrack(
                connector_name="spotify",
                connector_track_identifier=ext_id,
                title="T",
                artists={"names": ["A"]},
                raw_metadata={},
                last_updated=datetime.now(UTC),
            )
            db_session.add(ct)
            await db_session.flush()
            m = DBTrackMapping(
                track_id=db_track.id,
                connector_track_id=ct.id,
                connector_name="spotify",
                match_method="search",
                confidence=conf,
                is_primary=False,
            )
            db_session.add(m)
        await db_session.flush()

        await connector_repo.ensure_primary_for_connector(db_track.id, "spotify")

        remaining = await connector_repo.get_remaining_mappings(db_track.id, "spotify")
        primaries = [m for m in remaining if m.is_primary]
        assert len(primaries) == 1
        assert primaries[0].confidence == 90

    async def test_noop_when_primary_exists(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        track_id, _, _ = await _setup_track_with_mapping(db_session, is_primary=True)

        # Should not raise or change anything
        await connector_repo.ensure_primary_for_connector(track_id, "spotify")

        remaining = await connector_repo.get_remaining_mappings(track_id, "spotify")
        primaries = [m for m in remaining if m.is_primary]
        assert len(primaries) == 1


class TestGetConnectorTrackById:
    """get_connector_track_by_id returns domain ConnectorTrack entity."""

    async def test_returns_connector_track(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        _, ct_id, _ = await _setup_track_with_mapping(db_session)

        result = await connector_repo.get_connector_track_by_id(ct_id)

        assert result is not None
        assert result.connector_name == "spotify"

    async def test_returns_none_when_missing(
        self, db_session: AsyncSession, connector_repo
    ) -> None:
        result = await connector_repo.get_connector_track_by_id(uuid7())
        assert result is None
