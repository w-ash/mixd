"""Integration tests for track library API endpoints.

Tests the full request → route → use case → DB → response cycle.
Each test gets a fresh database via the client fixture.
"""

from uuid import UUID, uuid7

import httpx


async def _create_track(
    client: httpx.AsyncClient, title: str, artist: str = "Artist"
) -> UUID:
    """Create a track via playlist creation (tracks need to exist in DB).

    Since there's no direct track creation API, we insert tracks via
    the database. Use the internal test helper instead.
    """
    # Tracks are created via imports/playlist sync in production.
    # For API tests, we create them via the DB directly using the
    # application's own infrastructure.
    from src.application.runner import execute_use_case
    from src.domain.entities.track import Artist, Track

    track = Track(id=None, title=title, artists=[Artist(name=artist)])
    result = await execute_use_case(lambda uow: _save_track(uow, track))
    return result


async def _save_track(uow, track) -> UUID:
    """Save a track via UoW and return its ID."""
    async with uow:
        repo = uow.get_track_repository()
        saved = await repo.save_track(track)
        await uow.commit()
        return saved.id


class TestListTracksEndpoint:
    """GET /api/v1/tracks returns paginated track listing."""

    async def test_empty_library(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/tracks")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_returns_tracks(self, client: httpx.AsyncClient) -> None:
        await _create_track(client, "Creep", "Radiohead")
        await _create_track(client, "Yellow", "Coldplay")

        response = await client.get("/api/v1/tracks")

        body = response.json()
        assert body["total"] == 2
        assert len(body["data"]) == 2

    async def test_pagination(self, client: httpx.AsyncClient) -> None:
        for i in range(5):
            await _create_track(client, f"Track {i}")

        response = await client.get("/api/v1/tracks?limit=2&offset=2")

        body = response.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 2
        assert len(body["data"]) == 2

    async def test_search_by_title(self, client: httpx.AsyncClient) -> None:
        await _create_track(client, "Creep", "Radiohead")
        await _create_track(client, "Yellow", "Coldplay")

        response = await client.get("/api/v1/tracks?q=Creep")

        body = response.json()
        assert body["total"] == 1
        assert body["data"][0]["title"] == "Creep"

    async def test_search_by_artist(self, client: httpx.AsyncClient) -> None:
        await _create_track(client, "Creep", "Radiohead")
        await _create_track(client, "Yellow", "Coldplay")

        response = await client.get("/api/v1/tracks?q=Radiohead")

        body = response.json()
        assert body["total"] == 1

    async def test_search_min_length_validation(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/tracks?q=a")

        assert response.status_code == 422

    async def test_sort_parameter(self, client: httpx.AsyncClient) -> None:
        await _create_track(client, "Zebra")
        await _create_track(client, "Alpha")

        response = await client.get("/api/v1/tracks?sort=title_asc")

        body = response.json()
        titles = [t["title"] for t in body["data"]]
        assert titles == ["Alpha", "Zebra"]

    async def test_track_schema_fields(self, client: httpx.AsyncClient) -> None:
        await _create_track(client, "Test Song", "Test Artist")

        response = await client.get("/api/v1/tracks")

        track = response.json()["data"][0]
        assert "id" in track
        assert "title" in track
        assert "artists" in track
        assert "album" in track
        assert "duration_ms" in track
        assert "connector_names" in track
        assert "is_liked" in track


class TestGetTrackDetailEndpoint:
    """GET /api/v1/tracks/{id} returns full track details."""

    async def test_get_existing_track(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Creep", "Radiohead")

        response = await client.get(f"/api/v1/tracks/{track_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Creep"
        assert body["artists"][0]["name"] == "Radiohead"

    async def test_detail_schema_fields(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Test Song")

        response = await client.get(f"/api/v1/tracks/{track_id}")

        body = response.json()
        assert "connector_mappings" in body
        assert "like_status" in body
        assert "play_summary" in body
        assert "playlists" in body
        assert body["play_summary"]["total_plays"] == 0

    async def test_nonexistent_track_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"/api/v1/tracks/{uuid7()}")

        assert response.status_code == 404


class TestGetTrackPlaylistsEndpoint:
    """GET /api/v1/tracks/{id}/playlists returns playlist memberships."""

    async def test_track_with_no_playlists(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Lonely Track")

        response = await client.get(f"/api/v1/tracks/{track_id}/playlists")

        assert response.status_code == 200
        assert response.json() == []

    async def test_nonexistent_track_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"/api/v1/tracks/{uuid7()}/playlists")

        assert response.status_code == 404


class TestMergeTrackEndpoint:
    """POST /api/v1/tracks/{id}/merge merges a duplicate into the winner."""

    async def test_merge_returns_winner(self, client: httpx.AsyncClient) -> None:
        winner_id = await _create_track(client, "Creep", "Radiohead")
        loser_id = await _create_track(client, "Creep (Remaster)", "Radiohead")

        response = await client.post(
            f"/api/v1/tracks/{winner_id}/merge",
            json={"loser_id": str(loser_id)},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(winner_id)
        assert body["title"] == "Creep"

    async def test_loser_is_deleted_after_merge(
        self, client: httpx.AsyncClient
    ) -> None:
        winner_id = await _create_track(client, "Winner")
        loser_id = await _create_track(client, "Loser")

        await client.post(
            f"/api/v1/tracks/{winner_id}/merge",
            json={"loser_id": str(loser_id)},
        )

        response = await client.get(f"/api/v1/tracks/{loser_id}")
        assert response.status_code == 404

    async def test_self_merge_returns_400(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Solo")

        response = await client.post(
            f"/api/v1/tracks/{track_id}/merge",
            json={"loser_id": str(track_id)},
        )

        assert response.status_code == 400

    async def test_nonexistent_loser_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        winner_id = await _create_track(client, "Winner")

        response = await client.post(
            f"/api/v1/tracks/{winner_id}/merge",
            json={"loser_id": str(uuid7())},
        )

        assert response.status_code == 404


class TestListTracksFacets:
    """GET /api/v1/tracks?include_facets=true returns per-facet counts."""

    async def test_facets_default_absent(self, client: httpx.AsyncClient) -> None:
        """Without include_facets, facets field is absent/null."""
        await _create_track(client, "Song")
        response = await client.get("/api/v1/tracks")
        body = response.json()
        assert body.get("facets") is None

    async def test_facets_empty_library(self, client: httpx.AsyncClient) -> None:
        """Empty library returns zero-valued facets without errors."""
        response = await client.get("/api/v1/tracks?include_facets=true")
        assert response.status_code == 200
        facets = response.json()["facets"]
        assert facets is not None
        # preference is empty dict when there are no tracks
        assert facets["preference"] == {}
        assert facets["liked"] == {"true": 0, "false": 0}
        assert facets["connector"] == {}

    async def test_unrated_count_populated_when_tracks_have_no_preference(
        self, client: httpx.AsyncClient
    ) -> None:
        """Tracks without a preference row contribute to the 'unrated' bucket."""
        await _create_track(client, "A")
        await _create_track(client, "B")

        response = await client.get("/api/v1/tracks?include_facets=true")

        body = response.json()
        assert body["total"] == 2
        assert body["facets"]["preference"] == {"unrated": 2}
