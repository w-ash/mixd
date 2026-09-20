"""Integration tests for the artist API endpoints.

Full request -> route -> use case -> DB -> response, on a fresh database per
test. ``POST /artists/enrich`` is covered for its launch contract only — the
background task itself is stubbed out by the client fixture.
"""

from uuid import UUID, uuid7

import httpx2

from src.application.runner import execute_use_case
from src.domain.entities.artist import Artist
from src.domain.entities.track import ArtistCredit, Track
from tests.fixtures import TEST_USER_ID


async def _create_artist(name: str = "Radiohead") -> UUID:
    """Insert one canonical artist and return its id."""
    artist = Artist(name=name, user_id=TEST_USER_ID)

    async def _save(uow) -> UUID:
        async with uow:
            saved = await uow.get_artist_repository().save_artists([artist])
            await uow.commit()
            return saved[0].id

    return await execute_use_case(_save)


async def _create_track_credited_to(title: str, artist_id: UUID | None) -> UUID:
    """Insert a track, optionally resolving its single credit to an artist."""
    track = Track(
        id=None,
        title=title,
        artists=[ArtistCredit(credited_name="Radiohead")],
        user_id=TEST_USER_ID,
    )

    async def _save(uow) -> UUID:
        async with uow:
            repo = uow.get_track_repository()
            saved = await repo.save_track(track)
            if artist_id is not None:
                await repo.set_credit_artist_ids(
                    [(saved.id, 0, artist_id)], user_id=TEST_USER_ID
                )
            await uow.commit()
            return saved.id

    return await execute_use_case(_save)


class TestListArtistsEndpoint:
    """GET /api/v1/artists."""

    async def test_empty_library(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/artists")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_returns_artists(self, client: httpx2.AsyncClient) -> None:
        await _create_artist("Radiohead")
        await _create_artist("Coldplay")

        body = (await client.get("/api/v1/artists")).json()

        assert body["total"] == 2
        assert [a["name"] for a in body["data"]] == ["Coldplay", "Radiohead"]
        assert body["data"][0]["is_favorited"] is False
        assert body["data"][0]["track_count"] == 0
        assert body["data"][0]["connectors"] == []

    async def test_search_filters_by_name(self, client: httpx2.AsyncClient) -> None:
        await _create_artist("Radiohead")
        await _create_artist("Coldplay")

        body = (await client.get("/api/v1/artists", params={"search": "radio"})).json()

        assert [a["name"] for a in body["data"]] == ["Radiohead"]

    async def test_sort_descending(self, client: httpx2.AsyncClient) -> None:
        await _create_artist("Radiohead")
        await _create_artist("Coldplay")

        body = (
            await client.get("/api/v1/artists", params={"sort": "name_desc"})
        ).json()

        assert [a["name"] for a in body["data"]] == ["Radiohead", "Coldplay"]

    async def test_unknown_sort_is_rejected(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/artists", params={"sort": "nonsense"})

        assert response.status_code == 422

    async def test_favorites_only(self, client: httpx2.AsyncClient) -> None:
        favorited = await _create_artist("Radiohead")
        await _create_artist("Coldplay")
        await client.post(f"/api/v1/artists/{favorited}/favorite")

        body = (
            await client.get("/api/v1/artists", params={"favorites_only": True})
        ).json()

        assert [a["name"] for a in body["data"]] == ["Radiohead"]
        assert body["data"][0]["is_favorited"] is True

    async def test_track_count_reflects_credits(
        self, client: httpx2.AsyncClient
    ) -> None:
        artist_id = await _create_artist("Radiohead")
        await _create_track_credited_to("Creep", artist_id)
        await _create_track_credited_to("Idioteque", artist_id)

        body = (await client.get("/api/v1/artists")).json()

        assert body["data"][0]["track_count"] == 2


class TestArtistDetailEndpoint:
    """GET /api/v1/artists/{id}."""

    async def test_returns_detail(self, client: httpx2.AsyncClient) -> None:
        artist_id = await _create_artist("Radiohead")
        await _create_track_credited_to("Creep", artist_id)

        response = await client.get(f"/api/v1/artists/{artist_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(artist_id)
        assert body["name"] == "Radiohead"
        assert body["track_count"] == 1
        assert body["is_favorited"] is False
        assert body["connector_mappings"] == []
        assert body["related"] == []

    async def test_unknown_artist_404s(self, client: httpx2.AsyncClient) -> None:
        response = await client.get(f"/api/v1/artists/{uuid7()}")

        assert response.status_code == 404


class TestArtistFavoriteEndpoints:
    """POST / DELETE /api/v1/artists/{id}/favorite."""

    async def test_favorite_then_repeat_is_idempotent(
        self, client: httpx2.AsyncClient
    ) -> None:
        artist_id = await _create_artist()

        first = await client.post(f"/api/v1/artists/{artist_id}/favorite")
        second = await client.post(f"/api/v1/artists/{artist_id}/favorite")

        assert first.status_code == 200
        assert first.json() == {
            "artist_id": str(artist_id),
            "is_favorited": True,
            "changed": True,
        }
        assert second.status_code == 200
        assert second.json()["changed"] is False

    async def test_unfavorite_is_idempotent(self, client: httpx2.AsyncClient) -> None:
        artist_id = await _create_artist()
        await client.post(f"/api/v1/artists/{artist_id}/favorite")

        first = await client.delete(f"/api/v1/artists/{artist_id}/favorite")
        second = await client.delete(f"/api/v1/artists/{artist_id}/favorite")

        assert first.status_code == 204
        assert second.status_code == 204
        detail = (await client.get(f"/api/v1/artists/{artist_id}")).json()
        assert detail["is_favorited"] is False

    async def test_favorite_unknown_artist_404s(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.post(f"/api/v1/artists/{uuid7()}/favorite")

        assert response.status_code == 404

    async def test_unfavorite_unknown_artist_404s(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.delete(f"/api/v1/artists/{uuid7()}/favorite")

        assert response.status_code == 404

    async def test_favorite_count_reaches_the_dashboard(
        self, client: httpx2.AsyncClient
    ) -> None:
        artist_id = await _create_artist()
        await client.post(f"/api/v1/artists/{artist_id}/favorite")

        body = (await client.get("/api/v1/stats/dashboard")).json()

        assert body["total_favorite_artists"] == 1


class TestEnrichArtistsEndpoint:
    """POST /api/v1/artists/enrich."""

    async def test_returns_an_operation_handle(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.post("/api/v1/artists/enrich", json={"limit": 5})

        assert response.status_code == 200
        assert "operation_id" in response.json()

    async def test_rejects_a_zero_limit(self, client: httpx2.AsyncClient) -> None:
        response = await client.post("/api/v1/artists/enrich", json={"limit": 0})

        assert response.status_code == 422


class TestTracksByArtist:
    """GET /api/v1/tracks?artist_id=."""

    async def test_filters_to_the_artists_tracks(
        self, client: httpx2.AsyncClient
    ) -> None:
        artist_id = await _create_artist("Radiohead")
        await _create_track_credited_to("Creep", artist_id)
        await _create_track_credited_to("Unrelated", None)

        body = (
            await client.get("/api/v1/tracks", params={"artist_id": str(artist_id)})
        ).json()

        assert [t["title"] for t in body["data"]] == ["Creep"]
        assert body["data"][0]["artists"][0]["artist_id"] == str(artist_id)

    async def test_unknown_artist_yields_nothing(
        self, client: httpx2.AsyncClient
    ) -> None:
        await _create_track_credited_to("Creep", None)

        body = (
            await client.get("/api/v1/tracks", params={"artist_id": str(uuid7())})
        ).json()

        assert body["data"] == []
