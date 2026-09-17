"""Every mapping writer leaves each live (track, connector) pair with a primary.

Reads never elect (the mapper is a pure function of the row — v0.12.1
pre-flight 1), so the writers are the only place a primary can come from.
Each test drives one writer with the vacancy-creating shape and asserts
``find_missing_primary_violations`` is empty afterwards; reading the tracks
back under the suite's ``error::MissingPrimaryMappingWarning`` filter proves
the same thing from the display side.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities import Artist, ConnectorTrack, Track
from src.domain.repositories.connector import ConnectorMappingSpec
from src.infrastructure.persistence.database.db_models import DBTrackMapping
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


def _connector_track(identifier: str, *, title: str) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name="spotify",
        connector_track_identifier=identifier,
        title=title,
        artists=[Artist(name="Neon Priest")],
        album="Debut",
        duration_ms=200_000,
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )


async def _save_track(db_session: AsyncSession, title: str) -> Track:
    return (
        await get_unit_of_work(db_session)
        .get_track_repository()
        .save_track(
            Track(title=title, artists=[Artist(name="Neon Priest")], user_id="default")
        )
    )


async def _live_primary_ct_ids(db_session: AsyncSession, track_id: UUID) -> set[UUID]:
    rows = await db_session.execute(
        select(DBTrackMapping.connector_track_id).where(
            DBTrackMapping.track_id == track_id,
            DBTrackMapping.is_primary.is_(True),
            DBTrackMapping.superseded_at.is_(None),
        )
    )
    return set(rows.scalars().all())


async def _assert_no_vacancy(db_session: AsyncSession, *track_ids: UUID) -> None:
    uow = get_unit_of_work(db_session)
    assert await uow.get_connector_repository().find_missing_primary_violations() == []
    for track_id in track_ids:
        # Under ``filterwarnings = error::MissingPrimaryMappingWarning`` a
        # vacancy here would raise, not just warn.
        _ = await uow.get_track_repository().get_track_by_id(
            track_id, user_id="default"
        )


class TestMapTracksToConnectors:
    async def test_specs_without_primacy_still_get_a_primary(
        self, db_session: AsyncSession
    ):
        """The match pipeline's shape: ``primary=False`` on every spec."""
        track = await _save_track(db_session, f"Gold Rush {uuid4().hex[:8]}")
        connector_repo = get_unit_of_work(db_session).get_connector_repository()

        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=f"sp_low_{uuid4().hex[:8]}",
                match_method="artist_title",
                confidence=60,
            ),
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=f"sp_high_{uuid4().hex[:8]}",
                match_method="isrc",
                confidence=95,
            ),
        ])

        await _assert_no_vacancy(db_session, track.id)
        primaries = await _live_primary_ct_ids(db_session, track.id)
        assert len(primaries) == 1
        winner = (
            await db_session.execute(
                select(DBTrackMapping.confidence).where(
                    DBTrackMapping.connector_track_id.in_(primaries)
                )
            )
        ).scalar_one()
        assert winner == 95, "vacancy-fill elects the highest confidence"

    async def test_vacancy_fill_never_deposes_an_existing_primary(
        self, db_session: AsyncSession
    ):
        track = await _save_track(db_session, f"Gold Rush {uuid4().hex[:8]}")
        connector_repo = get_unit_of_work(db_session).get_connector_repository()
        incumbent = f"sp_incumbent_{uuid4().hex[:8]}"

        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=incumbent,
                match_method="direct_import",
                confidence=70,
                primary=True,
            )
        ])
        before = await _live_primary_ct_ids(db_session, track.id)
        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=f"sp_challenger_{uuid4().hex[:8]}",
                match_method="isrc",
                confidence=99,
            )
        ])

        assert await _live_primary_ct_ids(db_session, track.id) == before
        await _assert_no_vacancy(db_session, track.id)

    async def test_stale_id_secondary_beside_its_live_id(
        self, db_session: AsyncSession
    ):
        """The Spotify redirect shape: live id ``primary=True``, stale id
        ``primary=False`` for the same pair, in one batch."""
        track = await _save_track(db_session, f"Gold Rush {uuid4().hex[:8]}")
        connector_repo = get_unit_of_work(db_session).get_connector_repository()
        live = f"sp_live_{uuid4().hex[:8]}"

        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=f"sp_dead_{uuid4().hex[:8]}",
                match_method="direct_import_stale_id",
                confidence=100,
            ),
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=live,
                match_method="direct_import",
                confidence=100,
                primary=True,
            ),
        ])

        await _assert_no_vacancy(db_session, track.id)
        domain = (
            await get_unit_of_work(db_session)
            .get_track_repository()
            .get_track_by_id(track.id, user_id="default")
        )
        assert domain.connector_track_identifiers["spotify"] == live


class TestOtherWriters:
    async def test_map_track_to_connector_without_auto_primary(
        self, db_session: AsyncSession
    ):
        track = await _save_track(db_session, f"Gold Rush {uuid4().hex[:8]}")
        connector_repo = get_unit_of_work(db_session).get_connector_repository()

        await connector_repo.map_track_to_connector(
            track,
            "spotify",
            f"sp_{uuid4().hex[:8]}",
            "direct",
            100,
            auto_set_primary=False,
        )

        await _assert_no_vacancy(db_session, track.id)

    async def test_ingest_external_tracks_bulk(self, db_session: AsyncSession):
        uow = get_unit_of_work(db_session)
        tag = uuid4().hex[:8]

        tracks = await uow.get_connector_repository().ingest_external_tracks_bulk(
            "spotify",
            [
                _connector_track(f"sp_a_{tag}", title=f"Alpha {tag}"),
                _connector_track(f"sp_b_{tag}", title=f"Beta {tag}"),
            ],
            user_id="default",
        )

        await _assert_no_vacancy(db_session, *[t.id for t in tracks if t.id])

    async def test_merge_mappings_to_track(self, db_session: AsyncSession):
        uow = get_unit_of_work(db_session)
        tag = uuid4().hex[:8]
        (
            winner,
            loser,
        ) = await uow.get_connector_repository().ingest_external_tracks_bulk(
            "spotify",
            [
                _connector_track(f"sp_w_{tag}", title=f"Winner {tag}"),
                _connector_track(f"sp_l_{tag}", title=f"Loser {tag}"),
            ],
            user_id="default",
        )
        assert winner.id
        assert loser.id

        _ = await uow.get_track_repository().merge_mappings_to_track(
            loser.id, winner.id
        )

        await _assert_no_vacancy(db_session, winner.id)

    async def test_retire_then_relink(self, db_session: AsyncSession):
        """Unlink (retire the primary) elects the next one; relink onto a
        track with no primary elects there too."""
        uow = get_unit_of_work(db_session)
        connector_repo = uow.get_connector_repository()
        track = await _save_track(db_session, f"Gold Rush {uuid4().hex[:8]}")
        other = await _save_track(db_session, f"Other {uuid4().hex[:8]}")
        await connector_repo.map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=f"sp_first_{uuid4().hex[:8]}",
                match_method="direct_import",
                confidence=100,
                primary=True,
            ),
            ConnectorMappingSpec(
                track=track,
                connector="spotify",
                connector_id=f"sp_second_{uuid4().hex[:8]}",
                match_method="artist_title",
                confidence=80,
            ),
        ])
        primary_ct = (await _live_primary_ct_ids(db_session, track.id)).pop()
        primary_mapping_id = (
            await db_session.execute(
                select(DBTrackMapping.id).where(
                    DBTrackMapping.track_id == track.id,
                    DBTrackMapping.connector_track_id == primary_ct,
                )
            )
        ).scalar_one()

        # Retire the primary (the unlink use case's write) and re-elect.
        assert await uow.get_resolution_recorder().retire_mapping(
            primary_mapping_id, user_id="default", reason="manual"
        )
        await connector_repo.ensure_primary_for_connector(track.id, "spotify")
        await _assert_no_vacancy(db_session, track.id)

        # Relink the survivor onto ``other`` (the relink use case's writes).
        survivor_id = (
            await db_session.execute(
                select(DBTrackMapping.id).where(
                    DBTrackMapping.track_id == track.id,
                    DBTrackMapping.superseded_at.is_(None),
                )
            )
        ).scalar_one()
        await connector_repo.update_mapping_track(
            survivor_id, other.id, "manual_override", user_id="default"
        )
        await connector_repo.ensure_primary_for_connector(track.id, "spotify")
        await connector_repo.ensure_primary_for_connector(other.id, "spotify")
        await _assert_no_vacancy(db_session, track.id, other.id)
