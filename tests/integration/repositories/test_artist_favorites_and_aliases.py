"""Integration tests for artist favorites and the alias cache.

Favorites are presence rows on a composite key: favoriting twice is one row,
unfavoriting twice reports the second as a no-op, and every read is
tenant-scoped. Aliases hang off the connector artist and are replaced
wholesale, because a refresh must be able to drop a name MusicBrainz retired.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

from sqlalchemy import select

from src.domain.entities.artist import Artist, ArtistAlias
from src.infrastructure.persistence.database.models import DBArtistFavorite
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_connector_artist


def _user() -> str:
    return f"artist-fav-{uuid7()}"


async def _artist(repo, user_id: str, name: str = "Caribou") -> Artist:
    (saved,) = await repo.save_artists([Artist(name=name, user_id=user_id)])
    return saved


async def _favorited_at(db_session, user_id: str, artist_id: UUID) -> datetime | None:
    """Read the stored timestamp straight off the presence row."""
    result = await db_session.execute(
        select(DBArtistFavorite.favorited_at).where(
            DBArtistFavorite.user_id == user_id,
            DBArtistFavorite.artist_id == artist_id,
        )
    )
    return result.scalar_one()


class TestFavorites:
    async def test_favorite_is_idempotent_and_unfavorite_reports_the_delete(
        self, db_session
    ):
        uow = get_unit_of_work(db_session)
        artists, favorites = (
            uow.get_artist_repository(),
            uow.get_artist_favorite_repository(),
        )
        user_id = _user()
        artist = await _artist(artists, user_id)

        assert await favorites.favorite(artist.id, user_id=user_id) is True
        assert await favorites.favorite(artist.id, user_id=user_id) is False
        assert await favorites.get_favorite_artist_ids(user_id=user_id) == frozenset({
            artist.id
        })

        assert await favorites.unfavorite(artist.id, user_id=user_id) is True
        assert await favorites.unfavorite(artist.id, user_id=user_id) is False
        assert await favorites.get_favorite_artist_ids(user_id=user_id) == frozenset()

    async def test_refavoriting_keeps_the_original_timestamp(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, favorites = (
            uow.get_artist_repository(),
            uow.get_artist_favorite_repository(),
        )
        user_id = _user()
        artist = await _artist(artists, user_id)
        await favorites.favorite(artist.id, user_id=user_id)
        first = await _favorited_at(db_session, user_id, artist.id)

        await favorites.favorite(artist.id, user_id=user_id)

        assert await _favorited_at(db_session, user_id, artist.id) == first

    async def test_status_batch_and_id_set_read_presence(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, favorites = (
            uow.get_artist_repository(),
            uow.get_artist_favorite_repository(),
        )
        user_id = _user()
        kept = await _artist(artists, user_id, "Kept")
        plain = await _artist(artists, user_id, "Plain")
        await favorites.favorite(kept.id, user_id=user_id)

        status = await favorites.get_favorite_status_batch(
            [kept.id, plain.id], user_id=user_id
        )

        assert status == {kept.id}
        assert await favorites.get_favorite_artist_ids(user_id=user_id) == frozenset({
            kept.id
        })
        assert await favorites.get_favorite_status_batch([], user_id=user_id) == set()

    async def test_favorites_are_per_tenant(self, db_session):
        uow = get_unit_of_work(db_session)
        artists, favorites = (
            uow.get_artist_repository(),
            uow.get_artist_favorite_repository(),
        )
        owner, other = _user(), _user()
        artist = await _artist(artists, owner)
        await favorites.favorite(artist.id, user_id=owner)

        assert await favorites.get_favorite_artist_ids(user_id=other) == frozenset()
        assert await favorites.unfavorite(artist.id, user_id=other) is False
        assert await favorites.get_favorite_artist_ids(user_id=owner) == frozenset({
            artist.id
        })


class TestAliases:
    async def test_replace_is_a_replacement_not_a_merge(self, db_session):
        uow = get_unit_of_work(db_session)
        connectors, aliases = (
            uow.get_artist_connector_repository(),
            uow.get_artist_alias_repository(),
        )
        stored = await connectors.bulk_upsert_connector_artists(
            "musicbrainz",
            [make_connector_artist("mb-1", connector_name="musicbrainz")],
        )
        connector_artist_id = stored["mb-1"].id

        written = await aliases.replace_aliases(
            connector_artist_id,
            [
                ArtistAlias(
                    connector_artist_id=connector_artist_id,
                    name="TEED",
                    alias_type="Artist name",
                    locale="en",
                    fetched_at=datetime.now(UTC),
                ),
                ArtistAlias(
                    connector_artist_id=connector_artist_id,
                    name="Totally Enormous Extinct Dinosaurs",
                ),
            ],
        )
        assert written == 2

        # The refresh drops a name the service retired.
        replaced = await aliases.replace_aliases(
            connector_artist_id,
            [ArtistAlias(connector_artist_id=connector_artist_id, name="TEED")],
        )

        assert replaced == 1
        found = await aliases.get_aliases_for_connector_artists([connector_artist_id])
        assert [a.name for a in found[connector_artist_id]] == ["TEED"]

    async def test_replacing_with_nothing_clears_the_cache(self, db_session):
        uow = get_unit_of_work(db_session)
        connectors, aliases = (
            uow.get_artist_connector_repository(),
            uow.get_artist_alias_repository(),
        )
        stored = await connectors.bulk_upsert_connector_artists(
            "musicbrainz",
            [make_connector_artist("mb-1", connector_name="musicbrainz")],
        )
        connector_artist_id = stored["mb-1"].id
        await aliases.replace_aliases(
            connector_artist_id,
            [ArtistAlias(connector_artist_id=connector_artist_id, name="TEED")],
        )

        assert await aliases.replace_aliases(connector_artist_id, []) == 0
        assert (
            await aliases.get_aliases_for_connector_artists([connector_artist_id]) == {}
        )

    async def test_lookup_by_name_is_case_insensitive_and_keyed_as_asked(
        self, db_session
    ):
        uow = get_unit_of_work(db_session)
        connectors, aliases = (
            uow.get_artist_connector_repository(),
            uow.get_artist_alias_repository(),
        )
        stored = await connectors.bulk_upsert_connector_artists(
            "musicbrainz",
            [make_connector_artist("mb-1", connector_name="musicbrainz")],
        )
        connector_artist_id = stored["mb-1"].id
        await aliases.replace_aliases(
            connector_artist_id,
            [ArtistAlias(connector_artist_id=connector_artist_id, name="TEED")],
        )

        found = await aliases.find_connector_artist_ids_by_alias(["teed", "nobody"])

        assert found == {"teed": [connector_artist_id]}
        assert await aliases.find_connector_artist_ids_by_alias([]) == {}
