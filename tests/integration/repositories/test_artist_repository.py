"""Integration tests for :class:`ArtistRepository`.

The canonical artist round trip, the listing's every sort (including the two
that order by something ``artists`` does not store), its search, favorites
filter and keyset paging, the enrichment queue, and the tenant scoping that
*is* the isolation in production.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from src.domain.entities.artist import Artist
from src.domain.entities.track import ArtistCredit, Track
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_connector_artist


def _user() -> str:
    return f"artist-user-{uuid7()}"


async def _save_artists(repo, user_id: str, *names: str) -> list[Artist]:
    return await repo.save_artists([
        Artist(name=name, user_id=user_id) for name in names
    ])


async def _credit_tracks(track_repo, user_id: str, artist: Artist, count: int) -> None:
    """Persist ``count`` tracks crediting ``artist`` through the dual write."""
    await track_repo.save_tracks([
        Track(
            id=None,
            user_id=user_id,
            title=f"{artist.name} {index}",
            artists=[ArtistCredit(credited_name=artist.name, artist_id=artist.id)],
        )
        for index in range(count)
    ])


class TestSaveAndGet:
    async def test_save_returns_rows_in_input_order_with_their_own_ids(
        self, db_session
    ):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        wanted = [
            Artist(name="Caribou", user_id=user_id, mbid="mb-1", kind="person"),
            Artist(name="Jungle", user_id=user_id),
        ]

        saved = await repo.save_artists(wanted)

        assert [a.name for a in saved] == ["Caribou", "Jungle"]
        assert [a.id for a in saved] == [a.id for a in wanted]
        assert saved[0].mbid == "mb-1"
        assert saved[0].kind == "person"
        assert saved[0].created_at is not None

    async def test_get_by_id_is_scoped_to_the_owner(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id, other = _user(), _user()
        (artist,) = await _save_artists(repo, user_id, "Four Tet")

        assert (await repo.get_artist_by_id(artist.id, user_id=user_id)) is not None
        assert (await repo.get_artist_by_id(artist.id, user_id=other)) is None

    async def test_empty_batches_do_not_query(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()

        assert await repo.save_artists([]) == []
        assert await repo.count_tracks_by_artist([], user_id=_user()) == {}


class TestListingSorts:
    async def test_name_sorts_both_directions(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        await _save_artists(repo, user_id, "Caribou", "Aphex Twin", "Bonobo")

        ascending = await repo.list_artists(user_id=user_id, sort_by="name_asc")
        descending = await repo.list_artists(user_id=user_id, sort_by="name_desc")

        assert [a.name for a in ascending["artists"]] == [
            "Aphex Twin",
            "Bonobo",
            "Caribou",
        ]
        assert [a.name for a in descending["artists"]] == [
            "Caribou",
            "Bonobo",
            "Aphex Twin",
        ]
        assert ascending["total"] == 3

    async def test_track_count_sort_orders_by_a_column_the_table_does_not_have(
        self, db_session
    ):
        uow = get_unit_of_work(db_session)
        repo, track_repo = uow.get_artist_repository(), uow.get_track_repository()
        user_id = _user()
        few, many, none = await _save_artists(repo, user_id, "Few", "Many", "None")
        await _credit_tracks(track_repo, user_id, few, 1)
        await _credit_tracks(track_repo, user_id, many, 3)

        page = await repo.list_artists(user_id=user_id, sort_by="track_count_desc")

        assert [a.name for a in page["artists"]] == ["Many", "Few", "None"]
        assert page["track_counts"] == {many.id: 3, few.id: 1, none.id: 0}

        ascending = await repo.list_artists(user_id=user_id, sort_by="track_count_asc")
        assert [a.name for a in ascending["artists"]] == ["None", "Few", "Many"]

    async def test_favorited_at_sort_puts_unfavorited_artists_last(self, db_session):
        uow = get_unit_of_work(db_session)
        repo, favorites = (
            uow.get_artist_repository(),
            uow.get_artist_favorite_repository(),
        )
        user_id = _user()
        first, second, never = await _save_artists(
            repo, user_id, "First", "Second", "Never"
        )
        await favorites.favorite(first.id, user_id=user_id)
        await favorites.favorite(second.id, user_id=user_id)

        page = await repo.list_artists(user_id=user_id, sort_by="favorited_at_desc")

        names = [a.name for a in page["artists"]]
        assert names[-1] == "Never"
        assert set(names[:2]) == {"First", "Second"}
        assert page["favorited_ids"] == {first.id, second.id}


class TestListingFilters:
    async def test_query_matches_a_substring_case_insensitively(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        await _save_artists(repo, user_id, "Totally Enormous Extinct Dinosaurs", "TEED")

        page = await repo.list_artists(user_id=user_id, query="enormous")

        assert [a.name for a in page["artists"]] == [
            "Totally Enormous Extinct Dinosaurs"
        ]
        assert page["total"] == 1

    async def test_favorites_only_keeps_the_favorited_rows(self, db_session):
        uow = get_unit_of_work(db_session)
        repo, favorites = (
            uow.get_artist_repository(),
            uow.get_artist_favorite_repository(),
        )
        user_id = _user()
        kept, dropped = await _save_artists(repo, user_id, "Kept", "Dropped")
        await favorites.favorite(kept.id, user_id=user_id)

        page = await repo.list_artists(user_id=user_id, favorites_only=True)

        assert [a.id for a in page["artists"]] == [kept.id]
        assert dropped.id not in page["favorited_ids"]

    async def test_connector_names_come_back_per_artist(self, db_session):
        uow = get_unit_of_work(db_session)
        repo, connectors = (
            uow.get_artist_repository(),
            uow.get_artist_connector_repository(),
        )
        user_id = _user()
        (artist,) = await _save_artists(repo, user_id, "Mapped")
        stored = await connectors.bulk_upsert_connector_artists(
            "spotify",
            [make_connector_artist("sp-1", name="Mapped")],
        )
        await connectors.assert_mappings([
            {
                "user_id": user_id,
                "artist_id": artist.id,
                "connector_artist_id": stored["sp-1"].id,
                "connector_name": "spotify",
                "match_method": "direct",
                "confidence": 90,
            }
        ])

        page = await repo.list_artists(user_id=user_id)

        assert page["connector_names"] == {artist.id: ["spotify"]}


class TestListingPaging:
    async def test_keyset_paging_walks_the_whole_list_once(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        await _save_artists(repo, user_id, "A", "B", "C", "D", "E")

        seen: list[str] = []
        cursor: tuple[object, UUID] | None = None
        for _ in range(3):
            page = await repo.list_artists(
                user_id=user_id,
                limit=2,
                after_value=None if cursor is None else cursor[0],
                after_id=None if cursor is None else cursor[1],
                include_total=cursor is None,
            )
            seen.extend(a.name for a in page["artists"])
            cursor = page["next_page_key"]
            if cursor is None:
                break

        assert seen == ["A", "B", "C", "D", "E"]
        assert cursor is None

    async def test_an_exactly_full_last_page_emits_no_next_key(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        await _save_artists(repo, user_id, "A", "B", "C", "D")

        first = await repo.list_artists(user_id=user_id, limit=2)
        assert first["next_page_key"] is not None
        second = await repo.list_artists(
            user_id=user_id,
            limit=2,
            after_value=first["next_page_key"][0],
            after_id=first["next_page_key"][1],
        )

        assert [a.name for a in second["artists"]] == ["C", "D"]
        assert second["next_page_key"] is None

    async def test_track_count_cursor_carries_the_computed_value(self, db_session):
        uow = get_unit_of_work(db_session)
        repo, track_repo = uow.get_artist_repository(), uow.get_track_repository()
        user_id = _user()
        many, few = await _save_artists(repo, user_id, "Many", "Few")
        await _credit_tracks(track_repo, user_id, many, 2)
        await _credit_tracks(track_repo, user_id, few, 1)

        first = await repo.list_artists(
            user_id=user_id, sort_by="track_count_desc", limit=1
        )

        assert first["next_page_key"] == (2, many.id)
        second = await repo.list_artists(
            user_id=user_id,
            sort_by="track_count_desc",
            limit=1,
            after_value=first["next_page_key"][0],
            after_id=first["next_page_key"][1],
        )
        assert [a.name for a in second["artists"]] == ["Few"]

    async def test_include_total_false_skips_the_count(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        await _save_artists(repo, user_id, "A")

        page = await repo.list_artists(user_id=user_id, include_total=False)

        assert page["total"] is None
        assert len(page["artists"]) == 1


class TestEnrichmentQueue:
    async def test_artists_without_an_mbid_are_queued_oldest_first(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        anchored, unknown = await _save_artists(repo, user_id, "Anchored", "Unknown")
        await repo.set_identity(
            anchored.id, user_id=user_id, mbid="mb-anchor", kind="group"
        )

        queued = await repo.list_needing_enrichment(user_id=user_id)

        assert [a.id for a in queued] == [unknown.id]

    async def test_older_than_widens_the_queue_to_stale_anchors(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        (anchored,) = await _save_artists(repo, user_id, "Anchored")
        await repo.set_identity(anchored.id, user_id=user_id, mbid="mb", kind=None)

        assert await repo.list_needing_enrichment(user_id=user_id) == []
        widened = await repo.list_needing_enrichment(
            user_id=user_id, older_than=datetime.now(UTC) + timedelta(minutes=1)
        )

        assert [a.id for a in widened] == [anchored.id]

    async def test_touch_moves_updated_at(self, db_session):
        repo = get_unit_of_work(db_session).get_artist_repository()
        user_id = _user()
        (artist,) = await _save_artists(repo, user_id, "Touched")
        before = artist.updated_at

        await repo.touch([artist.id], user_id=user_id)

        refreshed = await repo.get_artist_by_id(artist.id, user_id=user_id)
        assert refreshed is not None
        assert before is not None
        assert refreshed.updated_at is not None
        assert refreshed.updated_at >= before


class TestCredits:
    async def test_counts_read_the_credit_rows(self, db_session):
        uow = get_unit_of_work(db_session)
        repo, track_repo = uow.get_artist_repository(), uow.get_track_repository()
        user_id = _user()
        (artist,) = await _save_artists(repo, user_id, "Counted")
        await _credit_tracks(track_repo, user_id, artist, 2)

        counts = await repo.count_tracks_by_artist([artist.id], user_id=user_id)

        assert counts == {artist.id: 2}


class TestTenantIsolation:
    async def test_another_tenants_artists_are_never_listed_or_counted(
        self, db_session
    ):
        uow = get_unit_of_work(db_session)
        repo, track_repo = uow.get_artist_repository(), uow.get_track_repository()
        mine, theirs = _user(), _user()
        (my_artist,) = await _save_artists(repo, mine, "Shared Name")
        (their_artist,) = await _save_artists(repo, theirs, "Shared Name")
        await _credit_tracks(track_repo, theirs, their_artist, 2)

        page = await repo.list_artists(user_id=mine)

        assert [a.id for a in page["artists"]] == [my_artist.id]
        assert await repo.count_tracks_by_artist([their_artist.id], user_id=mine) == {
            their_artist.id: 0
        }
