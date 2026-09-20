"""Integration tests for the artist connector stack.

The connector-artist cache (where a rename is a touch, never a second row),
the lookups that reach a canonical artist through a mapping, and the generic
mapping mechanism running against the real ``artist_mappings`` table — assert,
election, and an event recorded as ``entity_kind='artist'``.
"""

from uuid import uuid7

from sqlalchemy import select

from src.domain.entities.artist import Artist, ConnectorArtist
from src.domain.repositories.mapping import PrimaryCandidate
from src.infrastructure.persistence.database.models import (
    DBArtistMapping,
    DBResolutionEvent,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_connector_artist


def _user() -> str:
    return f"artist-conn-{uuid7()}"


async def _artist(repo, user_id: str, name: str = "Caribou") -> Artist:
    (saved,) = await repo.save_artists([Artist(name=name, user_id=user_id)])
    return saved


def _mapping_row(
    user_id: str, artist_id, connector_artist_id, *, confidence: int = 90
) -> dict[str, object]:
    return {
        "user_id": user_id,
        "artist_id": artist_id,
        "connector_artist_id": connector_artist_id,
        "connector_name": "spotify",
        "match_method": "direct",
        "confidence": confidence,
    }


class TestConnectorArtistCache:
    async def test_upsert_returns_rows_keyed_by_identifier(self, db_session):
        connectors = get_unit_of_work(db_session).get_artist_connector_repository()

        stored = await connectors.bulk_upsert_connector_artists(
            "spotify",
            [
                make_connector_artist("sp-1", name="Caribou"),
                make_connector_artist("sp-2", name="Daphni"),
            ],
        )

        assert set(stored) == {"sp-1", "sp-2"}
        assert stored["sp-1"].name == "Caribou"
        assert stored["sp-1"].connector_name == "spotify"

    async def test_a_rename_touches_the_row_instead_of_adding_one(self, db_session):
        connectors = get_unit_of_work(db_session).get_artist_connector_repository()
        first = await connectors.bulk_upsert_connector_artists(
            "spotify", [make_connector_artist("sp-1", name="Kanye West")]
        )

        renamed = await connectors.bulk_upsert_connector_artists(
            "spotify",
            [
                ConnectorArtist(
                    connector_name="spotify",
                    connector_artist_identifier="sp-1",
                    name="Ye",
                    raw_metadata={"disambiguation": "US rapper"},
                )
            ],
        )

        # Same row — the id every mapping references survives the rename.
        assert renamed["sp-1"].id == first["sp-1"].id
        assert renamed["sp-1"].name == "Ye"
        assert renamed["sp-1"].raw_metadata == {"disambiguation": "US rapper"}

    async def test_an_empty_batch_writes_nothing(self, db_session):
        connectors = get_unit_of_work(db_session).get_artist_connector_repository()

        assert await connectors.bulk_upsert_connector_artists("spotify", []) == {}


class TestLookupsThroughMappings:
    async def test_find_by_connector_artist_ids(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, connectors = (
            uow.get_artist_repository(),
            uow.get_artist_connector_repository(),
        )
        user_id = _user()
        artist = await _artist(artists, user_id)
        stored = await connectors.bulk_upsert_connector_artists(
            "spotify", [make_connector_artist("sp-1", name="Caribou")]
        )
        await connectors.assert_mappings([
            _mapping_row(user_id, artist.id, stored["sp-1"].id)
        ])

        by_row_id = await connectors.find_artists_by_connector_artist_ids(
            [stored["sp-1"].id], user_id=user_id
        )

        assert by_row_id[stored["sp-1"].id].id == artist.id

    async def test_another_tenants_mapping_is_never_followed(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, connectors = (
            uow.get_artist_repository(),
            uow.get_artist_connector_repository(),
        )
        owner, intruder = _user(), _user()
        artist = await _artist(artists, owner)
        stored = await connectors.bulk_upsert_connector_artists(
            "spotify", [make_connector_artist("sp-1")]
        )
        await connectors.assert_mappings([
            _mapping_row(owner, artist.id, stored["sp-1"].id)
        ])

        assert (
            await connectors.find_artists_by_connector_artist_ids(
                [stored["sp-1"].id], user_id=intruder
            )
            == {}
        )

    async def test_full_mappings_carry_the_connector_payload(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, connectors = (
            uow.get_artist_repository(),
            uow.get_artist_connector_repository(),
        )
        user_id = _user()
        artist = await _artist(artists, user_id)
        stored = await connectors.bulk_upsert_connector_artists(
            "musicbrainz",
            [
                ConnectorArtist(
                    connector_name="musicbrainz",
                    connector_artist_identifier="mb-1",
                    name="Caribou",
                    raw_metadata={"type": "Person"},
                )
            ],
        )
        await connectors.assert_mappings([
            {
                **_mapping_row(user_id, artist.id, stored["mb-1"].id),
                "connector_name": "musicbrainz",
            }
        ])

        (info,) = await connectors.get_full_mappings_for_artist(
            artist.id, user_id=user_id
        )

        assert info["connector_name"] == "musicbrainz"
        assert info["connector_artist_identifier"] == "mb-1"
        assert info["name"] == "Caribou"
        assert info["raw_metadata"] == {"type": "Person"}
        assert info["confidence"] == 90


class TestMappingMechanism:
    async def test_assert_creates_then_touches_then_rewrites(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, connectors = (
            uow.get_artist_repository(),
            uow.get_artist_connector_repository(),
        )
        user_id = _user()
        artist = await _artist(artists, user_id)
        other = await _artist(artists, user_id, "Daphni")
        stored = await connectors.bulk_upsert_connector_artists(
            "spotify", [make_connector_artist("sp-1")]
        )
        connector_artist_id = stored["sp-1"].id

        created = await connectors.assert_mappings([
            _mapping_row(user_id, artist.id, connector_artist_id, confidence=60)
        ])
        touched = await connectors.assert_mappings([
            _mapping_row(user_id, artist.id, connector_artist_id, confidence=60)
        ])
        rewritten = await connectors.assert_mappings([
            _mapping_row(user_id, other.id, connector_artist_id, confidence=95)
        ])

        assert len(created.created) == 1
        assert touched.touched == created.created
        assert rewritten.rewritten == created.created
        # No supersession columns: one live row, rewritten in place.
        result = await db_session.execute(
            select(DBArtistMapping)
            .where(DBArtistMapping.connector_artist_id == connector_artist_id)
            .execution_options(populate_existing=True)
        )
        (row,) = result.scalars().all()
        assert row.artist_id == other.id
        assert row.confidence == 95

    async def test_ensure_primaries_fills_a_vacancy(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, connectors = (
            uow.get_artist_repository(),
            uow.get_artist_connector_repository(),
        )
        user_id = _user()
        artist = await _artist(artists, user_id)
        stored = await connectors.bulk_upsert_connector_artists(
            "spotify",
            [make_connector_artist("sp-1"), make_connector_artist("sp-2")],
        )
        await connectors.assert_mappings([
            _mapping_row(user_id, artist.id, stored["sp-1"].id, confidence=70),
            _mapping_row(user_id, artist.id, stored["sp-2"].id, confidence=90),
        ])

        promoted = await connectors.ensure_primaries(
            [PrimaryCandidate(artist.id, "spotify", stored["sp-1"].id)], mode="fill"
        )
        occupied = await connectors.ensure_primaries(
            [PrimaryCandidate(artist.id, "spotify", stored["sp-2"].id)], mode="fill"
        )

        assert promoted == [PrimaryCandidate(artist.id, "spotify", stored["sp-1"].id)]
        assert occupied == []
        (info, _) = await connectors.get_full_mappings_for_artist(
            artist.id, user_id=user_id
        )
        assert info["is_primary"] is True
        assert info["connector_artist_identifier"] == "sp-1"

    async def test_recorded_assertion_is_an_artist_event(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, connectors = (
            uow.get_artist_repository(),
            uow.get_artist_connector_repository(),
        )
        user_id = _user()
        artist = await _artist(artists, user_id)
        stored = await connectors.bulk_upsert_connector_artists(
            "spotify", [make_connector_artist("sp-1")]
        )
        outcome = await connectors.assert_mappings([
            _mapping_row(user_id, artist.id, stored["sp-1"].id, confidence=88)
        ])

        await connectors.record_assertion(outcome)

        result = await db_session.execute(
            select(DBResolutionEvent).where(
                DBResolutionEvent.user_id == user_id,
                DBResolutionEvent.resulting_mapping_id == outcome.created[0],
            )
        )
        (event,) = result.scalars().all()
        assert event.entity_kind == "artist"
        assert event.track_id == artist.id
        assert event.connector_track_id == stored["sp-1"].id
        assert event.confidence == 88
