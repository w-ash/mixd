"""Integration tests for match review API endpoints.

Tests the full request -> route -> use case -> database -> response cycle
for the review queue endpoints.
"""

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text


async def _seed_review(client: httpx.AsyncClient) -> int:
    """Seed a match review by inserting raw data into the test database.

    Returns the review ID.
    """
    # Access the app's database directly for seeding
    from src.infrastructure.persistence.database.db_connection import get_session

    async with get_session() as session:
        now = datetime.now(UTC)

        # Create a track
        result = await session.execute(
            text(
                "INSERT INTO tracks (title, artists, created_at, updated_at) "
                "VALUES (:title, :artists, :now, :now) RETURNING id"
            ),
            {
                "title": "Test Track",
                "artists": '{"names": ["Test Artist"]}',
                "now": now,
            },
        )
        track_id = result.scalar_one()

        # Create a connector track
        result = await session.execute(
            text(
                "INSERT INTO connector_tracks "
                "(connector_name, connector_track_identifier, title, artists, "
                "raw_metadata, last_updated, created_at, updated_at) "
                "VALUES (:cn, :cti, :title, :artists, :rm, :now, :now, :now) "
                "RETURNING id"
            ),
            {
                "cn": "spotify",
                "cti": "sp_ct_test",
                "title": "Spotify Test Track",
                "artists": '{"names": ["Spotify Artist"]}',
                "rm": "{}",
                "now": now,
            },
        )
        ct_id = result.scalar_one()

        # Create a match review
        result = await session.execute(
            text(
                "INSERT INTO match_reviews "
                "(track_id, connector_name, connector_track_id, match_method, "
                "confidence, match_weight, status, created_at, updated_at) "
                "VALUES (:tid, :cn, :ctid, :mm, :conf, :mw, :status, :now, :now) "
                "RETURNING id"
            ),
            {
                "tid": track_id,
                "cn": "spotify",
                "ctid": ct_id,
                "mm": "artist_title",
                "conf": 72,
                "mw": 4.5,
                "status": "pending",
                "now": now,
            },
        )
        review_id = result.scalar_one()
        await session.commit()
        return review_id


class TestListReviews:
    """GET /api/v1/reviews — list pending match reviews."""

    async def test_empty_list(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/reviews")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_lists_seeded_review(self, client: httpx.AsyncClient):
        await _seed_review(client)
        response = await client.get("/api/v1/reviews")
        body = response.json()
        assert body["total"] >= 1
        assert len(body["data"]) >= 1
        review = body["data"][0]
        assert review["connector_name"] == "spotify"
        assert review["confidence"] == 72
        assert review["status"] == "pending"

    async def test_pagination_params(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/reviews?limit=10&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert body["limit"] == 10
        assert body["offset"] == 0


class TestResolveReview:
    """POST /api/v1/reviews/{id}/resolve — accept or reject."""

    async def test_reject_review(self, client: httpx.AsyncClient):
        review_id = await _seed_review(client)
        response = await client.post(
            f"/api/v1/reviews/{review_id}/resolve",
            json={"action": "reject"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["review"]["status"] == "rejected"
        assert body["mapping_created"] is False

    async def test_accept_review(self, client: httpx.AsyncClient):
        review_id = await _seed_review(client)
        response = await client.post(
            f"/api/v1/reviews/{review_id}/resolve",
            json={"action": "accept"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["review"]["status"] == "accepted"
        assert body["mapping_created"] is True

    async def test_resolve_nonexistent_returns_404(self, client: httpx.AsyncClient):
        response = await client.post(
            "/api/v1/reviews/99999/resolve",
            json={"action": "reject"},
        )
        assert response.status_code == 404

    async def test_invalid_action_returns_422(self, client: httpx.AsyncClient):
        review_id = await _seed_review(client)
        response = await client.post(
            f"/api/v1/reviews/{review_id}/resolve",
            json={"action": "invalid"},
        )
        assert response.status_code == 422
