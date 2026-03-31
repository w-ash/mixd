"""Integration tests for track mapping API endpoints (relink, unlink, set primary).

Tests the full request → route → use case → DB → response cycle.
Each test gets a fresh database via the client fixture.
"""

from uuid import UUID

import httpx

from tests.fixtures.factories import nonexistent_id


async def _create_track_with_mapping(
    client: httpx.AsyncClient,
    title: str = "Test Track",
    artist: str = "Artist",
    connector: str = "spotify",
    external_id: str = "spotify:test123",
) -> tuple[UUID, UUID]:
    """Create a track with a connector mapping. Returns (track_id, mapping_id)."""
    from src.application.runner import execute_use_case
    from src.domain.entities.track import Artist, Track

    async def _create(uow):
        async with uow:
            track_repo = uow.get_track_repository()
            connector_repo = uow.get_connector_repository()

            track = Track(id=None, title=title, artists=[Artist(name=artist)])
            saved = await track_repo.save_track(track)

            await connector_repo.map_track_to_connector(
                saved,
                connector,
                external_id,
                match_method="direct",
                confidence=100,
                auto_set_primary=True,
            )
            await uow.commit()

            mappings = await connector_repo.get_full_mappings_for_track(
                saved.id, user_id="default"
            )
            return saved.id, mappings[0]["mapping_id"]

    return await execute_use_case(_create)


async def _create_bare_track(
    client: httpx.AsyncClient, title: str, artist: str = "Artist"
) -> UUID:
    """Create a track with no connector mappings."""
    from src.application.runner import execute_use_case
    from src.domain.entities.track import Artist, Track

    async def _create(uow):
        async with uow:
            track_repo = uow.get_track_repository()
            track = Track(id=None, title=title, artists=[Artist(name=artist)])
            saved = await track_repo.save_track(track)
            await uow.commit()
            return saved.id

    return await execute_use_case(_create)


class TestRelinkMappingEndpoint:
    """PATCH /api/v1/tracks/{track_id}/mappings/{mapping_id} relinks a mapping."""

    async def test_relink_returns_updated_track(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)
        target_id = await _create_bare_track(client, "Target Track")

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}",
            json={"new_track_id": str(target_id)},
        )

        assert response.status_code == 200
        body = response.json()
        # After relink, the source track should have fewer mappings
        assert body["id"] == str(track_id)

    async def test_self_relink_returns_400(self, client: httpx.AsyncClient) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}",
            json={"new_track_id": str(track_id)},
        )

        assert response.status_code == 400

    async def test_nonexistent_target_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}",
            json={"new_track_id": nonexistent_id()},
        )

        assert response.status_code == 404

    async def test_nonexistent_mapping_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, _ = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{nonexistent_id()}",
            json={"new_track_id": nonexistent_id()},
        )

        assert response.status_code == 404

    async def test_track_id_mismatch_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)
        other_id = await _create_bare_track(client, "Other")

        response = await client.patch(
            f"/api/v1/tracks/{other_id}/mappings/{mapping_id}",
            json={"new_track_id": nonexistent_id()},
        )

        assert response.status_code == 400


class TestUnlinkMappingEndpoint:
    """DELETE /api/v1/tracks/{track_id}/mappings/{mapping_id} unlinks a mapping."""

    async def test_unlink_returns_result(self, client: httpx.AsyncClient) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.delete(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_mapping_id"] == str(mapping_id)
        # Last mapping → orphan track created
        assert body["orphan_track_id"] is not None

    async def test_nonexistent_mapping_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, _ = await _create_track_with_mapping(client)

        response = await client.delete(
            f"/api/v1/tracks/{track_id}/mappings/{nonexistent_id()}"
        )

        assert response.status_code == 404

    async def test_track_id_mismatch_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)
        other_id = await _create_bare_track(client, "Other")

        response = await client.delete(
            f"/api/v1/tracks/{other_id}/mappings/{mapping_id}"
        )

        assert response.status_code == 400


class TestSetPrimaryMappingEndpoint:
    """PATCH /api/v1/tracks/{track_id}/mappings/{mapping_id}/primary sets primary."""

    async def test_set_primary_returns_track(self, client: httpx.AsyncClient) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}/primary"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(track_id)

    async def test_nonexistent_mapping_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, _ = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{nonexistent_id()}/primary"
        )

        assert response.status_code == 404

    async def test_track_id_mismatch_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)
        other_id = await _create_bare_track(client, "Other")

        response = await client.patch(
            f"/api/v1/tracks/{other_id}/mappings/{mapping_id}/primary"
        )

        assert response.status_code == 400
