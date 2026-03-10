"""Integration tests for track mapping API endpoints (relink, unlink, set primary).

Tests the full request → route → use case → DB → response cycle.
Each test gets a fresh database via the client fixture.
"""

import httpx


async def _create_track_with_mapping(
    client: httpx.AsyncClient,
    title: str = "Test Track",
    artist: str = "Artist",
    connector: str = "spotify",
    external_id: str = "spotify:test123",
) -> tuple[int, int]:
    """Create a track with a connector mapping. Returns (track_id, mapping_id)."""
    from src.application.runner import execute_use_case
    from src.domain.entities.track import Artist, Track

    async def _create(uow):
        async with uow:
            track_repo = uow.get_track_repository()
            connector_repo = uow.get_connector_repository()

            track = Track(title=title, artists=[Artist(name=artist)])
            saved = await track_repo.save_track(track)

            await connector_repo.map_track_to_connector(
                saved, connector, external_id,
                match_method="direct", confidence=100,
                auto_set_primary=True,
            )
            await uow.commit()

            mappings = await connector_repo.get_full_mappings_for_track(saved.id)
            return saved.id, mappings[0]["mapping_id"]

    return await execute_use_case(_create)


async def _create_bare_track(
    client: httpx.AsyncClient, title: str, artist: str = "Artist"
) -> int:
    """Create a track with no connector mappings."""
    from src.application.runner import execute_use_case
    from src.domain.entities.track import Artist, Track

    async def _create(uow):
        async with uow:
            track_repo = uow.get_track_repository()
            track = Track(title=title, artists=[Artist(name=artist)])
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
            json={"new_track_id": target_id},
        )

        assert response.status_code == 200
        body = response.json()
        # After relink, the source track should have fewer mappings
        assert body["id"] == track_id

    async def test_self_relink_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}",
            json={"new_track_id": track_id},
        )

        assert response.status_code == 400

    async def test_nonexistent_target_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}",
            json={"new_track_id": 99999},
        )

        assert response.status_code == 404

    async def test_nonexistent_mapping_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, _ = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/99999",
            json={"new_track_id": 1},
        )

        assert response.status_code == 404

    async def test_track_id_mismatch_returns_400(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)
        other_id = await _create_bare_track(client, "Other")

        response = await client.patch(
            f"/api/v1/tracks/{other_id}/mappings/{mapping_id}",
            json={"new_track_id": 1},
        )

        assert response.status_code == 400


class TestUnlinkMappingEndpoint:
    """DELETE /api/v1/tracks/{track_id}/mappings/{mapping_id} unlinks a mapping."""

    async def test_unlink_returns_result(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.delete(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["deleted_mapping_id"] == mapping_id
        # Last mapping → orphan track created
        assert body["orphan_track_id"] is not None

    async def test_nonexistent_mapping_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, _ = await _create_track_with_mapping(client)

        response = await client.delete(
            f"/api/v1/tracks/{track_id}/mappings/99999"
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

    async def test_set_primary_returns_track(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, mapping_id = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/{mapping_id}/primary"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == track_id

    async def test_nonexistent_mapping_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id, _ = await _create_track_with_mapping(client)

        response = await client.patch(
            f"/api/v1/tracks/{track_id}/mappings/99999/primary"
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
