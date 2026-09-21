"""``connector_track_artists``: the connector-track writer keeps the credit rows.

Every ``connector_tracks`` write lands its credits beside it: the
``connector_artists`` records the credits name are ensured first, then one row
per credit pointing at its record (or at nothing, for a credit the service
gave no id). A connector credit is the service's own statement, so a
re-upsert takes the latest one outright, and the mapper reads the identifiers
back through the records.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from sqlalchemy import select

from src.domain.entities import ArtistCredit, ConnectorArtistCredit, ConnectorTrack
from src.domain.repositories.connector import ConnectorMappingSpec
from src.infrastructure.persistence.database.models import (
    DBConnectorArtist,
    DBConnectorTrack,
    DBConnectorTrackArtist,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


def _credit(
    name: str, identifier: str | None, join_phrase: str | None = None
) -> ConnectorArtistCredit:
    return ConnectorArtistCredit(
        credited_name=name,
        connector_artist_identifier=identifier,
        join_phrase=join_phrase,
    )


def _payload(
    identifier: str, *credits: ConnectorArtistCredit, connector: str = "spotify"
) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name=connector,
        connector_track_identifier=identifier,
        title=f"Song {identifier}",
        artists=list(credits),
        raw_metadata={
            "id": identifier,
            "artists": [
                {"id": c.connector_artist_identifier, "name": c.credited_name}
                for c in credits
                if c.connector_artist_identifier is not None
            ],
        },
        last_updated=datetime.now(UTC),
    )


async def _rows(
    session, connector_track_id: UUID
) -> list[tuple[int, str, str | None, str | None]]:
    """``(position, credited name, service artist id, join phrase)`` per credit row."""
    result = await session.execute(
        select(
            DBConnectorTrackArtist.position,
            DBConnectorTrackArtist.credited_name,
            DBConnectorArtist.connector_artist_identifier,
            DBConnectorTrackArtist.join_phrase,
        )
        .outerjoin(
            DBConnectorArtist,
            DBConnectorArtist.id == DBConnectorTrackArtist.connector_artist_id,
        )
        .where(DBConnectorTrackArtist.connector_track_id == connector_track_id)
        .order_by(DBConnectorTrackArtist.position)
    )
    return [tuple(row) for row in result.tuples()]


async def _connector_artist(session, connector: str, identifier: str):
    result = await session.execute(
        select(DBConnectorArtist).where(
            DBConnectorArtist.connector_name == connector,
            DBConnectorArtist.connector_artist_identifier == identifier,
        )
    )
    return result.scalar_one_or_none()


class TestUpsertConnectorTracksWritesCredits:
    async def test_one_row_per_credit_pointing_at_its_record(self, db_session):
        uow = get_unit_of_work(db_session)
        key = f"sp-{uuid7()}"
        payload = _payload(
            key,
            _credit("Thom Yorke", f"{key}-a1", " & "),
            _credit("PJ Harvey", f"{key}-a2"),
        )

        stored = await uow.get_connector_repository().upsert_connector_tracks(
            "spotify", [payload]
        )

        assert await _rows(db_session, stored[key].id) == [
            (0, "Thom Yorke", f"{key}-a1", " & "),
            (1, "PJ Harvey", f"{key}-a2", None),
        ]
        # The connector-artist record was ensured first, with the payload's
        # own per-artist dump as its metadata.
        record = await _connector_artist(db_session, "spotify", f"{key}-a1")
        assert record is not None
        assert record.name == "Thom Yorke"
        assert record.raw_metadata == {"id": f"{key}-a1", "name": "Thom Yorke"}
        # And the entity handed back already carries the credits written.
        assert stored[key].artists == payload.artists

    async def test_a_credit_without_an_id_points_at_nothing(self, db_session):
        uow = get_unit_of_work(db_session)
        key = f"ap-{uuid7()}"

        stored = await uow.get_connector_repository().upsert_connector_tracks(
            "apple", [_payload(key, _credit("Tycho", None), connector="apple")]
        )

        assert await _rows(db_session, stored[key].id) == [(0, "Tycho", None, None)]

    async def test_a_reupsert_takes_the_services_latest_statement(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_repository()
        key = f"sp-{uuid7()}"
        first = _payload(
            key, _credit("Alpha", f"{key}-a1"), _credit("Beta", f"{key}-a2")
        )
        (stored,) = (await repo.upsert_connector_tracks("spotify", [first])).values()

        # Position 0 is renamed and re-pointed, position 1 is gone.
        _ = await repo.upsert_connector_tracks(
            "spotify", [_payload(key, _credit("Alpha Prime", f"{key}-a9"))]
        )

        assert await _rows(db_session, stored.id) == [
            (0, "Alpha Prime", f"{key}-a9", None)
        ]

    async def test_a_record_already_stored_keeps_its_own_payload(self, db_session):
        """Insert-or-touch: a first-hand record is never rewritten by a credit."""
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_repository()
        key = f"sp-{uuid7()}"
        _ = await repo.upsert_connector_tracks(
            "spotify", [_payload(key, _credit("Caribou", f"{key}-a1"))]
        )

        _ = await repo.upsert_connector_tracks(
            "spotify", [_payload(f"{key}-b", _credit("Caribou (renamed)", f"{key}-a1"))]
        )

        record = await _connector_artist(db_session, "spotify", f"{key}-a1")
        assert record is not None
        assert record.name == "Caribou"

    async def test_the_mapper_round_trips_the_identifiers(self, db_session):
        uow = get_unit_of_work(db_session)
        repo = uow.get_connector_repository()
        key = f"sp-{uuid7()}"
        payload = _payload(
            key, _credit("Thom Yorke", f"{key}-a1", " & "), _credit("Solo", None)
        )
        (stored,) = (await repo.upsert_connector_tracks("spotify", [payload])).values()

        loaded = await repo.get_connector_track_by_id(stored.id)

        assert loaded is not None
        assert loaded.artists == payload.artists


async def _connector_track_id(session, connector: str, identifier: str) -> UUID:
    result = await session.execute(
        select(DBConnectorTrack.id).where(
            DBConnectorTrack.connector_name == connector,
            DBConnectorTrack.connector_track_identifier == identifier,
        )
    )
    return result.scalar_one()


class TestMappingSpecsWriteCredits:
    async def test_a_spec_with_credits_stores_them_with_ids(self, db_session):
        user_id = f"cta-{uuid7()}"
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            make_track(
                user_id=user_id,
                title="Odessa",
                artists=[ArtistCredit(credited_name="Caribou")],
            )
        )
        key = f"td-{uuid7()}"

        _ = await uow.get_connector_repository().map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector="tidal",
                connector_id=key,
                match_method="direct_import",
                confidence=100,
                metadata={"title": "Odessa"},
                credits=(_credit("Caribou", f"{key}-a1"),),
                primary=True,
            )
        ])

        row_id = await _connector_track_id(db_session, "tidal", key)
        assert await _rows(db_session, row_id) == [(0, "Caribou", f"{key}-a1", None)]

    async def test_a_spec_without_credits_stores_the_canonical_names(self, db_session):
        user_id = f"cta-{uuid7()}"
        uow = get_unit_of_work(db_session)
        track = await uow.get_track_repository().save_track(
            make_track(
                user_id=user_id,
                title="Kiara",
                artists=[ArtistCredit(credited_name="Bonobo")],
            )
        )
        key = f"lf-{uuid7()}"

        _ = await uow.get_connector_repository().map_tracks_to_connectors([
            ConnectorMappingSpec(
                track=track,
                connector="lastfm",
                connector_id=key,
                match_method="lastfm_import",
                confidence=90,
                primary=True,
            )
        ])

        row_id = await _connector_track_id(db_session, "lastfm", key)
        assert await _rows(db_session, row_id) == [(0, "Bonobo", None, None)]
