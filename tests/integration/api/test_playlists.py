"""Integration tests for playlist CRUD API endpoints.

Tests the full request → route handler → use case → database → response cycle
using httpx.AsyncClient with ASGITransport. Each test gets a fresh database
via the db_session fixture.
"""

import httpx


class TestListPlaylists:
    """GET /api/v1/playlists returns paginated playlist summaries."""

    async def test_empty_list(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/playlists")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_list_after_create(self, client: httpx.AsyncClient) -> None:
        await client.post("/api/v1/playlists", json={"name": "Test Playlist"})

        response = await client.get("/api/v1/playlists")

        body = response.json()
        assert body["total"] == 1
        assert body["data"][0]["name"] == "Test Playlist"
        assert body["data"][0]["track_count"] == 0

    async def test_pagination_params(self, client: httpx.AsyncClient) -> None:
        for i in range(3):
            await client.post("/api/v1/playlists", json={"name": f"Playlist {i}"})

        response = await client.get("/api/v1/playlists?limit=2&offset=1")

        body = response.json()
        assert body["total"] == 3
        assert body["limit"] == 2
        assert body["offset"] == 1
        assert len(body["data"]) == 2


class TestCreatePlaylist:
    """POST /api/v1/playlists creates a new playlist."""

    async def test_create_with_name_only(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/api/v1/playlists", json={"name": "My Playlist"})

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "My Playlist"
        assert body["description"] is None
        assert body["track_count"] == 0
        assert body["entries"] == []
        assert "id" in body

    async def test_create_with_description(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/playlists",
            json={"name": "Chill Vibes", "description": "Relaxing tunes"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Chill Vibes"
        assert body["description"] == "Relaxing tunes"

    async def test_create_empty_name_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/api/v1/playlists", json={"name": ""})

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    async def test_create_missing_name_returns_422(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/api/v1/playlists", json={})

        assert response.status_code == 422


class TestGetPlaylist:
    """GET /api/v1/playlists/{id} returns playlist detail."""

    async def test_get_existing_playlist(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/api/v1/playlists", json={"name": "Fetch Me"})
        playlist_id = create_resp.json()["id"]

        response = await client.get(f"/api/v1/playlists/{playlist_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Fetch Me"
        assert body["id"] == playlist_id
        assert "entries" in body

    async def test_get_nonexistent_returns_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/playlists/99999")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"


class TestUpdatePlaylist:
    """PATCH /api/v1/playlists/{id} updates playlist metadata."""

    async def test_update_name(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/api/v1/playlists", json={"name": "Original"})
        playlist_id = create_resp.json()["id"]

        response = await client.patch(
            f"/api/v1/playlists/{playlist_id}", json={"name": "Updated"}
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Updated"

    async def test_update_description(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/api/v1/playlists", json={"name": "Test"})
        playlist_id = create_resp.json()["id"]

        response = await client.patch(
            f"/api/v1/playlists/{playlist_id}",
            json={"description": "New description"},
        )

        assert response.status_code == 200
        assert response.json()["description"] == "New description"

    async def test_update_nonexistent_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.patch("/api/v1/playlists/99999", json={"name": "Nope"})

        assert response.status_code == 404


class TestDeletePlaylist:
    """DELETE /api/v1/playlists/{id} removes a playlist."""

    async def test_delete_existing(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/api/v1/playlists", json={"name": "Delete Me"})
        playlist_id = create_resp.json()["id"]

        response = await client.delete(f"/api/v1/playlists/{playlist_id}")

        assert response.status_code == 204

        # Verify deleted
        get_resp = await client.get(f"/api/v1/playlists/{playlist_id}")
        assert get_resp.status_code == 404

    async def test_delete_nonexistent_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.delete("/api/v1/playlists/99999")

        assert response.status_code == 404


class TestGetPlaylistTracks:
    """GET /api/v1/playlists/{id}/tracks returns paginated entries."""

    async def test_empty_playlist_tracks(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/api/v1/playlists", json={"name": "Empty"})
        playlist_id = create_resp.json()["id"]

        response = await client.get(f"/api/v1/playlists/{playlist_id}/tracks")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0

    async def test_nonexistent_playlist_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/playlists/99999/tracks")

        assert response.status_code == 404
