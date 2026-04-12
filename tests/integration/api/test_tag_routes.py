"""Integration tests for tag API endpoints.

Covers the full request → route → use case → DB → response cycle for:
- POST /api/v1/tracks/{id}/tags (add with normalization + 422 on invalid)
- DELETE /api/v1/tracks/{id}/tags/{tag} (URL-decoded lookup, idempotent 204)
- POST /api/v1/tracks/tags/batch (15k cap, atomic validation)
- GET /api/v1/tags (autocomplete with trigram-ish ILIKE)
- GET /api/v1/tracks?tag=... (tag filter flows through list_tracks)
"""

from uuid import UUID, uuid7

import httpx


async def _create_track(
    client: httpx.AsyncClient, title: str, artist: str = "Artist"
) -> UUID:
    from src.application.runner import execute_use_case
    from src.domain.entities.track import Artist, Track

    track = Track(id=None, title=title, artists=[Artist(name=artist)])

    async def _save(uow):
        async with uow:
            repo = uow.get_track_repository()
            saved = await repo.save_track(track)
            await uow.commit()
            return saved.id

    return await execute_use_case(_save)


class TestAddTrackTag:
    async def test_normalizes_and_returns_changed(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id = await _create_track(client, "Song")

        response = await client.post(
            f"/api/v1/tracks/{track_id}/tags", json={"tag": "Mood:Chill"}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["tag"] == "mood:chill"
        assert body["changed"] is True

    async def test_duplicate_returns_not_changed(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id = await _create_track(client, "Song")
        await client.post(f"/api/v1/tracks/{track_id}/tags", json={"tag": "mood:chill"})

        response = await client.post(
            f"/api/v1/tracks/{track_id}/tags", json={"tag": "mood:chill"}
        )

        assert response.status_code == 201
        assert response.json()["changed"] is False

    async def test_invalid_tag_returns_422(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Song")

        response = await client.post(
            f"/api/v1/tracks/{track_id}/tags", json={"tag": "cafe!"}
        )

        assert response.status_code == 422

    async def test_too_long_tag_returns_422(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Song")

        response = await client.post(
            f"/api/v1/tracks/{track_id}/tags", json={"tag": "a" * 65}
        )

        assert response.status_code == 422

    async def test_missing_track_returns_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            f"/api/v1/tracks/{uuid7()}/tags", json={"tag": "mood:chill"}
        )

        assert response.status_code == 404


class TestDeleteTrackTag:
    async def test_removes_existing_returns_204(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id = await _create_track(client, "Song")
        await client.post(f"/api/v1/tracks/{track_id}/tags", json={"tag": "mood:chill"})

        response = await client.delete(f"/api/v1/tracks/{track_id}/tags/mood:chill")

        assert response.status_code == 204

    async def test_missing_tag_returns_204(self, client: httpx.AsyncClient) -> None:
        """DELETE is idempotent — same response whether or not the tag existed."""
        track_id = await _create_track(client, "Song")

        response = await client.delete(f"/api/v1/tracks/{track_id}/tags/never-tagged")

        assert response.status_code == 204

    async def test_url_encoded_tag_normalized(self, client: httpx.AsyncClient) -> None:
        """Clients that URL-encode Mood%3AChill still match stored mood:chill."""
        track_id = await _create_track(client, "Song")
        await client.post(f"/api/v1/tracks/{track_id}/tags", json={"tag": "mood:chill"})

        response = await client.delete(f"/api/v1/tracks/{track_id}/tags/Mood%3AChill")

        assert response.status_code == 204

        detail = await client.get(f"/api/v1/tracks/{track_id}")
        assert "mood:chill" not in detail.json()["tags"]

    async def test_invalid_tag_returns_422(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Song")

        response = await client.delete(f"/api/v1/tracks/{track_id}/tags/cafe!")

        assert response.status_code == 422


class TestBatchTag:
    async def test_tags_multiple_tracks_atomically(
        self, client: httpx.AsyncClient
    ) -> None:
        ids = [await _create_track(client, f"Song-{i}") for i in range(3)]

        response = await client.post(
            "/api/v1/tracks/tags/batch",
            json={"track_ids": [str(i) for i in ids], "tag": "Mood:Chill"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["tag"] == "mood:chill"
        assert body["requested"] == 3
        assert body["tagged"] == 3

    async def test_invalid_tag_rejects_whole_batch_with_422(
        self, client: httpx.AsyncClient
    ) -> None:
        ids = [await _create_track(client, f"Song-{i}") for i in range(3)]

        response = await client.post(
            "/api/v1/tracks/tags/batch",
            json={"track_ids": [str(i) for i in ids], "tag": "cafe!"},
        )

        assert response.status_code == 422

        # None of the tracks got tagged.
        for tid in ids:
            detail = await client.get(f"/api/v1/tracks/{tid}")
            assert detail.json()["tags"] == []

    async def test_over_15k_returns_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/tracks/tags/batch",
            json={
                "track_ids": [str(uuid7()) for _ in range(15_001)],
                "tag": "mood:chill",
            },
        )

        assert response.status_code == 422

    async def test_returns_tagged_count_for_mix(
        self, client: httpx.AsyncClient
    ) -> None:
        """Repo skips duplicates via ON CONFLICT; ``tagged`` reflects real inserts."""
        ids = [await _create_track(client, f"Song-{i}") for i in range(3)]
        await client.post(f"/api/v1/tracks/{ids[0]}/tags", json={"tag": "mood:chill"})

        response = await client.post(
            "/api/v1/tracks/tags/batch",
            json={"track_ids": [str(i) for i in ids], "tag": "mood:chill"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["requested"] == 3
        assert body["tagged"] == 2


class TestListTags:
    async def test_empty_when_no_tags(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/tags")
        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_tags_with_counts(self, client: httpx.AsyncClient) -> None:
        ids = [await _create_track(client, f"Song-{i}") for i in range(3)]
        for tid in ids:
            await client.post(f"/api/v1/tracks/{tid}/tags", json={"tag": "mood:chill"})
        await client.post(f"/api/v1/tracks/{ids[0]}/tags", json={"tag": "banger"})

        response = await client.get("/api/v1/tags")

        body = response.json()
        tags_by_name = {t["tag"]: t["count"] for t in body}
        assert tags_by_name == {"mood:chill": 3, "banger": 1}

    async def test_query_filters_results(self, client: httpx.AsyncClient) -> None:
        track_id = await _create_track(client, "Song")
        for t in ("mood:chill", "energy:high", "banger"):
            await client.post(f"/api/v1/tracks/{track_id}/tags", json={"tag": t})

        response = await client.get("/api/v1/tags?q=mood")

        body = response.json()
        assert [t["tag"] for t in body] == ["mood:chill"]


class TestTrackListingTagFilter:
    async def test_filter_by_single_tag(self, client: httpx.AsyncClient) -> None:
        tagged = await _create_track(client, "Tagged")
        _untagged = await _create_track(client, "Untagged")
        await client.post(f"/api/v1/tracks/{tagged}/tags", json={"tag": "mood:chill"})

        response = await client.get("/api/v1/tracks?tag=mood:chill")

        body = response.json()
        assert [UUID(t["id"]) for t in body["data"]] == [tagged]

    async def test_and_mode_intersection(self, client: httpx.AsyncClient) -> None:
        both = await _create_track(client, "Both")
        only_one = await _create_track(client, "Only one")
        for t in ("mood:chill", "energy:low"):
            await client.post(f"/api/v1/tracks/{both}/tags", json={"tag": t})
        await client.post(f"/api/v1/tracks/{only_one}/tags", json={"tag": "mood:chill"})

        response = await client.get(
            "/api/v1/tracks?tag=mood:chill&tag=energy:low&tag_mode=and"
        )

        assert [UUID(t["id"]) for t in response.json()["data"]] == [both]

    async def test_invalid_tag_query_returns_422(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/tracks?tag=cafe!")
        assert response.status_code == 422

    async def test_tags_included_in_detail_response(
        self, client: httpx.AsyncClient
    ) -> None:
        track_id = await _create_track(client, "Song")
        await client.post(f"/api/v1/tracks/{track_id}/tags", json={"tag": "mood:chill"})
        await client.post(f"/api/v1/tracks/{track_id}/tags", json={"tag": "banger"})

        response = await client.get(f"/api/v1/tracks/{track_id}")

        assert set(response.json()["tags"]) == {"mood:chill", "banger"}
