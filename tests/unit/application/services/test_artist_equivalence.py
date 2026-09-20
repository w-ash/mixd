"""The per-batch alias lookup: two queries, however many names the batch credits."""

from uuid import UUID, uuid7

from src.application.services.artist_equivalence import build_artist_equivalence
from src.domain.entities.artist import ArtistAlias
from tests.fixtures import make_mock_artist_alias_repo, make_mock_uow

TEED = "TEED"
FULL_NAME = "Totally Enormous Extinct Dinosaurs"


def alias(connector_artist_id: UUID, name: str) -> ArtistAlias:
    return ArtistAlias(connector_artist_id=connector_artist_id, name=name)


def uow_with(connector_artist_id: UUID, *names: str):
    repo = make_mock_artist_alias_repo(
        find_connector_artist_ids_by_alias={
            name: [connector_artist_id] for name in names
        },
        get_aliases_for_connector_artists={
            connector_artist_id: [alias(connector_artist_id, name) for name in names]
        },
    )
    return make_mock_uow(artist_alias_repo=repo), repo


class TestBuildArtistEquivalence:
    async def test_cached_aliases_become_one_group(self):
        uow, _ = uow_with(uuid7(), TEED, FULL_NAME)

        equivalence = await build_artist_equivalence(uow, [FULL_NAME, TEED])

        assert equivalence.same(TEED, FULL_NAME)

    async def test_the_batch_is_queried_once_with_distinct_names(self):
        uow, repo = uow_with(uuid7(), TEED, FULL_NAME)

        await build_artist_equivalence(uow, [TEED, TEED, " ", FULL_NAME, ""])

        repo.find_connector_artist_ids_by_alias.assert_awaited_once_with([
            TEED,
            FULL_NAME,
        ])

    async def test_no_names_skips_the_database_entirely(self):
        uow, repo = uow_with(uuid7(), TEED)

        equivalence = await build_artist_equivalence(uow, ["", "   "])

        assert equivalence.groups == {}
        repo.find_connector_artist_ids_by_alias.assert_not_awaited()

    async def test_an_unenriched_library_short_circuits_after_one_query(self):
        repo = make_mock_artist_alias_repo(find_connector_artist_ids_by_alias={})
        uow = make_mock_uow(artist_alias_repo=repo)

        equivalence = await build_artist_equivalence(uow, [FULL_NAME])

        assert equivalence.groups == {}
        repo.get_aliases_for_connector_artists.assert_not_awaited()
