"""Integration tests for playlist assignment API endpoints.

Covers POST create-and-apply, POST re-apply, DELETE, and the
``current_assignments`` extension on the Spotify browser endpoint.
"""

from uuid import UUID, uuid4, uuid7

import httpx

from src.application.runner import execute_use_case
from tests.fixtures import make_connector_playlist


async def _seed_cp() -> UUID:
    """Persist a bare ConnectorPlaylist via the public upsert path.

    No canonical Playlist or PlaylistLink — we exercise the assignment
    flow's Epic 7 decoupling (assignments bind to DBConnectorPlaylist).
    """
    uid = uuid4().hex[:8]
    cp = make_connector_playlist(
        connector_playlist_identifier=f"sp_{uid}",
        name=f"CP {uid}",
    )

    async def _do(uow):
        async with uow:
            saved = await uow.get_connector_playlist_repository().bulk_upsert_models([
                cp
            ])
            await uow.commit()
            return saved[0].id

    return await execute_use_case(_do)


class TestCreateAssignment:
    async def test_422_on_invalid_preference_value(
        self, client: httpx.AsyncClient
    ) -> None:
        cp_id = await _seed_cp()

        response = await client.post(
            "/api/v1/playlist-assignments",
            json={
                "connector_playlist_id": str(cp_id),
                "action_type": "set_preference",
                "action_value": "love",
            },
        )

        assert response.status_code == 422
        body = response.json()
        assert "must be one of" in str(body).lower()

    async def test_422_on_invalid_tag_value(self, client: httpx.AsyncClient) -> None:
        cp_id = await _seed_cp()

        response = await client.post(
            "/api/v1/playlist-assignments",
            json={
                "connector_playlist_id": str(cp_id),
                "action_type": "add_tag",
                "action_value": "bad/tag",
            },
        )

        assert response.status_code == 422

    async def test_creates_and_returns_assignment_plus_result(
        self, client: httpx.AsyncClient
    ) -> None:
        cp_id = await _seed_cp()

        response = await client.post(
            "/api/v1/playlist-assignments",
            json={
                "connector_playlist_id": str(cp_id),
                "action_type": "add_tag",
                "action_value": "Mood:Chill",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["assignment"]["action_type"] == "add_tag"
        # action_value normalized to lower-case canonical form
        assert body["assignment"]["action_value"] == "mood:chill"
        assert body["assignment"]["connector_playlist_id"] == str(cp_id)
        # Result shape includes all engine counters
        assert "assignments_processed" in body["result"]
        assert "tags_applied" in body["result"]


class TestApplyAssignment:
    async def test_reapply_existing_assignment(self, client: httpx.AsyncClient) -> None:
        cp_id = await _seed_cp()

        created = await client.post(
            "/api/v1/playlist-assignments",
            json={
                "connector_playlist_id": str(cp_id),
                "action_type": "add_tag",
                "action_value": "mood:chill",
            },
        )
        assert created.status_code == 201
        assignment_id = created.json()["assignment"]["id"]

        response = await client.post(
            f"/api/v1/playlist-assignments/{assignment_id}/apply"
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["assignments_processed"] == 1

    async def test_apply_unknown_id_returns_zero_processed(
        self, client: httpx.AsyncClient
    ) -> None:
        """Unknown id simply yields nothing — engine is idempotent."""
        response = await client.post(f"/api/v1/playlist-assignments/{uuid7()}/apply")

        assert response.status_code == 200
        assert response.json()["assignments_processed"] == 0


class TestDeleteAssignment:
    async def test_delete_succeeds_with_204(self, client: httpx.AsyncClient) -> None:
        cp_id = await _seed_cp()
        created = await client.post(
            "/api/v1/playlist-assignments",
            json={
                "connector_playlist_id": str(cp_id),
                "action_type": "add_tag",
                "action_value": "mood:chill",
            },
        )
        assignment_id = created.json()["assignment"]["id"]

        response = await client.delete(f"/api/v1/playlist-assignments/{assignment_id}")

        assert response.status_code == 204

    async def test_delete_missing_returns_404(self, client: httpx.AsyncClient) -> None:
        response = await client.delete(f"/api/v1/playlist-assignments/{uuid7()}")

        assert response.status_code == 404


class TestCurrentAssignmentsOnPickerRows:
    async def test_picker_row_exposes_active_assignments(
        self, client: httpx.AsyncClient
    ) -> None:
        cp_id = await _seed_cp()
        await client.post(
            "/api/v1/playlist-assignments",
            json={
                "connector_playlist_id": str(cp_id),
                "action_type": "add_tag",
                "action_value": "mood:chill",
            },
        )
        await client.post(
            "/api/v1/playlist-assignments",
            json={
                "connector_playlist_id": str(cp_id),
                "action_type": "set_preference",
                "action_value": "star",
            },
        )

        response = await client.get("/api/v1/connectors/spotify/playlists")

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        row = next(r for r in data if r["connector_playlist_db_id"] == str(cp_id))
        action_types = {a["action_type"] for a in row["current_assignments"]}
        assert action_types == {"add_tag", "set_preference"}

    async def test_picker_row_empty_when_no_assignments(
        self, client: httpx.AsyncClient
    ) -> None:
        cp_id = await _seed_cp()

        response = await client.get("/api/v1/connectors/spotify/playlists")

        data = response.json()["data"]
        row = next(r for r in data if r["connector_playlist_db_id"] == str(cp_id))
        assert row["current_assignments"] == []
