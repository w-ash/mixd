"""Integration tests for playlist link API endpoints.

Tests GET/DELETE link endpoints with a real database.
POST create and POST sync require a live connector (Spotify API),
so they're tested at the unit level with mocks.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx

from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBPlaylistMapping,
)
from tests.fixtures.factories import nonexistent_id


async def _seed_link(client: httpx.AsyncClient) -> tuple[str, str]:
    """Create a playlist via API, then seed a link directly in the DB.

    Returns (playlist_id, link_id) as string UUIDs.
    """
    # Create playlist via API (exercises the full stack)
    resp = await client.post(
        "/api/v1/playlists", json={"name": f"Linked {uuid4().hex[:6]}"}
    )
    playlist_id_str: str = resp.json()["id"]
    playlist_id = UUID(playlist_id_str)

    # Seed connector playlist + mapping directly (no real Spotify needed)
    uid = uuid4().hex[:8]
    async with get_session() as session:
        db_cp = DBConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier=f"sp_{uid}",
            name=f"My Spotify Playlist {uid}",
            description="A test playlist",
            owner="testuser",
            owner_id="user123",
            is_public=True,
            collaborative=False,
            follower_count=10,
            items=[],
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        session.add(db_cp)
        await session.flush()

        db_mapping = DBPlaylistMapping(
            playlist_id=playlist_id,
            connector_name="spotify",
            connector_playlist_id=db_cp.id,
            sync_direction="push",
            sync_status="synced",
            last_sync_completed_at=datetime.now(UTC),
            last_sync_tracks_added=5,
            last_sync_tracks_removed=1,
        )
        session.add(db_mapping)
        await session.flush()
        link_id = str(db_mapping.id)
        await session.commit()

    return playlist_id_str, link_id


class TestListPlaylistLinks:
    """GET /api/v1/playlists/{id}/links"""

    async def test_empty_links(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/api/v1/playlists", json={"name": "No Links"})
        playlist_id = resp.json()["id"]

        response = await client.get(f"/api/v1/playlists/{playlist_id}/links")

        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_link_details(self, client: httpx.AsyncClient) -> None:
        playlist_id, _link_id = await _seed_link(client)

        response = await client.get(f"/api/v1/playlists/{playlist_id}/links")

        assert response.status_code == 200
        links = response.json()
        assert len(links) == 1

        link = links[0]
        assert link["connector_name"] == "spotify"
        assert link["sync_direction"] == "push"
        assert link["sync_status"] == "synced"
        assert link["connector_playlist_id"].startswith("sp_")
        assert link["connector_playlist_name"].startswith("My Spotify Playlist")
        assert link["last_sync_tracks_added"] == 5
        assert link["last_sync_tracks_removed"] == 1


class TestDeletePlaylistLink:
    """DELETE /api/v1/playlists/{id}/links/{link_id}"""

    async def test_delete_existing_link(self, client: httpx.AsyncClient) -> None:
        playlist_id, link_id = await _seed_link(client)

        response = await client.delete(
            f"/api/v1/playlists/{playlist_id}/links/{link_id}"
        )

        assert response.status_code == 204

        # Verify link is gone
        links_resp = await client.get(f"/api/v1/playlists/{playlist_id}/links")
        assert links_resp.json() == []

    async def test_delete_nonexistent_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        resp = await client.post("/api/v1/playlists", json={"name": "Del Test"})
        playlist_id = resp.json()["id"]

        response = await client.delete(
            f"/api/v1/playlists/{playlist_id}/links/{nonexistent_id()}"
        )

        assert response.status_code == 404


class TestPlaylistDetailIncludesLinks:
    """GET /api/v1/playlists/{id} returns connector_links from links."""

    async def test_detail_shows_link_briefs(self, client: httpx.AsyncClient) -> None:
        playlist_id, _link_id = await _seed_link(client)

        response = await client.get(f"/api/v1/playlists/{playlist_id}")

        assert response.status_code == 200
        body = response.json()
        assert len(body["connector_links"]) == 1
        link = body["connector_links"][0]
        assert link["connector_name"] == "spotify"
        assert link["sync_direction"] == "push"
        assert link["sync_status"] == "synced"


class TestListPlaylistsIncludesLinkBriefs:
    """GET /api/v1/playlists list includes connector_links as brief objects."""

    async def test_list_has_link_briefs(self, client: httpx.AsyncClient) -> None:
        playlist_id, _link_id = await _seed_link(client)

        response = await client.get("/api/v1/playlists")

        assert response.status_code == 200
        playlists = response.json()["data"]
        target = next(p for p in playlists if p["id"] == playlist_id)
        assert len(target["connector_links"]) >= 1
        link = target["connector_links"][0]
        assert link["connector_name"] == "spotify"


class TestCreatePlaylistLinkValidation:
    """POST /api/v1/playlists/{id}/links — validation edge cases."""

    async def test_invalid_connector_returns_422(
        self, client: httpx.AsyncClient
    ) -> None:
        resp = await client.post("/api/v1/playlists", json={"name": "Validate"})
        playlist_id = resp.json()["id"]

        response = await client.post(
            f"/api/v1/playlists/{playlist_id}/links",
            json={
                "connector": "",
                "connector_playlist_id": "abc123",
                "sync_direction": "push",
            },
        )

        # Empty connector should be rejected (either 400 or 422)
        assert response.status_code in (400, 422)
