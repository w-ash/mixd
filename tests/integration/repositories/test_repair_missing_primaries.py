"""Bulk repair of (track, connector) pairs whose live mappings have no primary.

The state under test is legacy stock: every writer elects a primary now and no
read repairs one, so a vacancy is a permanent FAIL on the
``missing_primary_mappings`` integrity check with nothing else to clear it.

Real Postgres throughout — the election is a ``DISTINCT ON`` and the promotion a
correlated ``NOT EXISTS`` update with ``RETURNING``, none of which has a
meaningful mock.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.persistence.database.models import (
    DBConnectorTrack,
    DBTrack,
    DBTrackMapping,
)
from src.infrastructure.persistence.repositories.track.connector import (
    TrackConnectorRepository,
)

_USER = "default"
_OTHER_USER = "someone-else"


@pytest.fixture
def connector_repo(db_session: AsyncSession) -> TrackConnectorRepository:
    return TrackConnectorRepository(db_session)


async def _make_track(db_session: AsyncSession, *, user_id: str = _USER) -> UUID:
    uid = uuid4().hex[:8]
    track = DBTrack(
        user_id=user_id,
        title=f"Track {uid}",
        artists={"names": [f"Artist {uid}"]},
    )
    db_session.add(track)
    await db_session.flush()
    return track.id


async def _make_connector_track(
    db_session: AsyncSession, *, connector: str = "spotify"
) -> tuple[UUID, str]:
    uid = uuid4().hex[:8]
    identifier = f"{connector[:2]}_{uid}"
    ct = DBConnectorTrack(
        connector_name=connector,
        connector_track_identifier=identifier,
        title=f"CT {uid}",
        artists={"names": [f"Artist {uid}"]},
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(ct)
    await db_session.flush()
    return ct.id, identifier


async def _add_mapping(
    db_session: AsyncSession,
    *,
    track_id: UUID,
    connector_track_id: UUID,
    connector: str = "spotify",
    confidence: int,
    is_primary: bool = False,
    user_id: str = _USER,
    mapping_id: UUID | None = None,
    match_method: str = "isrc_match",
) -> UUID:
    mapping = DBTrackMapping(
        id=mapping_id or uuid7(),
        user_id=user_id,
        track_id=track_id,
        connector_track_id=connector_track_id,
        connector_name=connector,
        match_method=match_method,
        confidence=confidence,
        is_primary=is_primary,
    )
    db_session.add(mapping)
    await db_session.flush()
    return mapping.id


async def _primary_mapping_ids(db_session: AsyncSession, track_id: UUID) -> set[UUID]:
    result = await db_session.execute(
        select(DBTrackMapping.id)
        .where(
            DBTrackMapping.track_id == track_id,
            DBTrackMapping.is_primary.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    return set(result.scalars().all())


async def _primary_identifier(db_session: AsyncSession, track_id: UUID) -> str | None:
    """The external id the track's primary spotify mapping names, if any."""
    result = await db_session.execute(
        select(DBConnectorTrack.connector_track_identifier)
        .join(
            DBTrackMapping,
            DBTrackMapping.connector_track_id == DBConnectorTrack.id,
        )
        .where(
            DBTrackMapping.track_id == track_id,
            DBTrackMapping.connector_name == "spotify",
            DBTrackMapping.is_primary.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


class TestElection:
    async def test_promotes_the_highest_confidence_mapping(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        weak_ct, _ = await _make_connector_track(db_session)
        strong_ct, strong_identifier = await _make_connector_track(db_session)
        _ = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=weak_ct, confidence=40
        )
        winner = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=strong_ct, confidence=95
        )

        repaired = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert [r.mapping_id for r in repaired] == [winner]
        assert await _primary_mapping_ids(db_session, track_id) == {winner}
        assert await _primary_identifier(db_session, track_id) == strong_identifier

    async def test_breaks_a_confidence_tie_on_the_lowest_id(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        first_ct, first_identifier = await _make_connector_track(db_session)
        second_ct, _ = await _make_connector_track(db_session)
        # uuid7 is time-ordered, so the pair is generated in the order it sorts.
        first_id, second_id = sorted((uuid7(), uuid7()))
        _ = await _add_mapping(
            db_session,
            track_id=track_id,
            connector_track_id=second_ct,
            confidence=70,
            mapping_id=second_id,
        )
        _ = await _add_mapping(
            db_session,
            track_id=track_id,
            connector_track_id=first_ct,
            confidence=70,
            mapping_id=first_id,
        )

        repaired = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert [r.mapping_id for r in repaired] == [first_id]
        assert await _primary_identifier(db_session, track_id) == first_identifier

    async def test_repairs_each_connector_of_a_track_independently(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        spotify_ct, _ = await _make_connector_track(db_session)
        lastfm_ct, _ = await _make_connector_track(db_session, connector="lastfm")
        _ = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=spotify_ct, confidence=50
        )
        _ = await _add_mapping(
            db_session,
            track_id=track_id,
            connector_track_id=lastfm_ct,
            connector="lastfm",
            confidence=50,
        )

        repaired = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert {r.connector_name for r in repaired} == {"spotify", "lastfm"}


class TestWhatItLeavesAlone:
    async def test_a_pair_that_already_has_a_primary(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        incumbent_ct, _ = await _make_connector_track(db_session)
        challenger_ct, _ = await _make_connector_track(db_session)
        incumbent = await _add_mapping(
            db_session,
            track_id=track_id,
            connector_track_id=incumbent_ct,
            confidence=30,
            is_primary=True,
        )
        _ = await _add_mapping(
            db_session,
            track_id=track_id,
            connector_track_id=challenger_ct,
            confidence=99,
        )

        repaired = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert repaired == []
        assert await _primary_mapping_ids(db_session, track_id) == {incumbent}

    async def test_another_tenants_vacancy(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        other_track = await _make_track(db_session, user_id=_OTHER_USER)
        other_ct, _ = await _make_connector_track(db_session)
        _ = await _add_mapping(
            db_session,
            track_id=other_track,
            connector_track_id=other_ct,
            confidence=80,
            user_id=_OTHER_USER,
        )

        repaired = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert repaired == []
        assert await _primary_mapping_ids(db_session, other_track) == set()

    async def test_a_superseded_mapping_is_not_a_candidate(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        live_ct, live_identifier = await _make_connector_track(db_session)
        dead_ct, _ = await _make_connector_track(db_session)
        live = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=live_ct, confidence=20
        )
        retired = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=dead_ct, confidence=99
        )
        _ = await db_session.execute(
            update(DBTrackMapping)
            .where(DBTrackMapping.id == retired)
            .values(superseded_at=datetime.now(UTC), supersession_reason="rematch")
        )

        repaired = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert [r.mapping_id for r in repaired] == [live]
        assert await _primary_identifier(db_session, track_id) == live_identifier

    async def test_a_stale_id_row_is_never_the_winner(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ):
        """A stale-id row caches a dead id, and a pair's primary names its
        current identity. It is never a candidate, even when it carries the
        highest confidence."""
        track_id = await _make_track(db_session)
        stale_ct, _ = await _make_connector_track(db_session)
        live_ct, live_identifier = await _make_connector_track(db_session)
        await _add_mapping(
            db_session,
            track_id=track_id,
            connector_track_id=stale_ct,
            confidence=100,
            match_method="direct_import_stale_id",
        )
        live_mapping = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=live_ct, confidence=60
        )

        repaired = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert [r.mapping_id for r in repaired] == [live_mapping]
        assert await _primary_mapping_ids(db_session, track_id) == {live_mapping}
        assert await _primary_identifier(db_session, track_id) == live_identifier

    async def test_a_pair_with_only_stale_id_rows_is_not_a_vacancy(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ):
        track_id = await _make_track(db_session)
        stale_ct, _ = await _make_connector_track(db_session)
        await _add_mapping(
            db_session,
            track_id=track_id,
            connector_track_id=stale_ct,
            confidence=100,
            match_method="direct_import_stale_id",
        )

        assert await connector_repo.repair_missing_primaries(user_id=_USER) == []
        assert await _primary_mapping_ids(db_session, track_id) == set()
        assert await connector_repo.find_missing_primary_violations() == []


class TestIdempotenceAndDryRun:
    async def test_a_second_run_finds_nothing(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        _ = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=ct_id, confidence=60
        )

        first = await connector_repo.repair_missing_primaries(user_id=_USER)
        second = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert len(first) == 1
        assert second == []

    async def test_dry_run_reports_without_writing(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        mapping_id = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=ct_id, confidence=60
        )

        planned = await connector_repo.repair_missing_primaries(
            user_id=_USER, dry_run=True
        )

        assert [r.mapping_id for r in planned] == [mapping_id]
        assert await _primary_mapping_ids(db_session, track_id) == set()
        assert await _primary_identifier(db_session, track_id) is None


class TestRepairClearsTheIntegrityCheck:
    async def test_no_violations_remain(
        self, db_session: AsyncSession, connector_repo: TrackConnectorRepository
    ) -> None:
        track_id = await _make_track(db_session)
        ct_id, _ = await _make_connector_track(db_session)
        _ = await _add_mapping(
            db_session, track_id=track_id, connector_track_id=ct_id, confidence=60
        )
        assert await connector_repo.find_missing_primary_violations() != []

        _ = await connector_repo.repair_missing_primaries(user_id=_USER)

        assert await connector_repo.find_missing_primary_violations() == []
