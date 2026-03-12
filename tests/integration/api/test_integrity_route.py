"""Integration tests for the data integrity API endpoint.

Tests GET /api/v1/stats/integrity returns correct structure
with all six checks.
"""

import httpx
import pytest


class TestGetIntegrityReport:
    async def test_returns_200_with_all_checks(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/stats/integrity")
        assert response.status_code == 200

        data = response.json()
        assert "checks" in data
        assert "overall_status" in data
        assert "total_issues" in data
        assert len(data["checks"]) == 6

    async def test_empty_db_all_pass(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/stats/integrity")
        data = response.json()

        assert data["overall_status"] == "pass"
        assert data["total_issues"] == 0

        for check in data["checks"]:
            assert check["status"] == "pass"
            assert check["count"] == 0

    async def test_check_names_present(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/stats/integrity")
        data = response.json()

        names = {c["name"] for c in data["checks"]}
        assert names == {
            "multiple_primary_mappings",
            "missing_primary_mappings",
            "orphaned_connector_tracks",
            "duplicate_tracks",
            "stale_pending_reviews",
            "pending_reviews",
        }

    async def test_check_schema_structure(self, client: httpx.AsyncClient):
        response = await client.get("/api/v1/stats/integrity")
        data = response.json()

        for check in data["checks"]:
            assert isinstance(check["name"], str)
            assert check["status"] in ("pass", "warn", "fail")
            assert isinstance(check["count"], int)
            assert isinstance(check["details"], list)
