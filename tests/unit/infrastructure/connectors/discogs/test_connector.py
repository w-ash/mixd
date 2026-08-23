"""Unit tests for the ``DiscogsConnector`` facade's snapshot seam.

The facade satisfies the application's ``DiscogsCollectionConnector``
protocol structurally: collection pages are validated by the Pydantic model
at the client boundary, then cross the seam as plain JSON (``model_dump``).
These tests pin that dumped shape — the exact keys the snapshot use case
narrows — with the underlying API client mocked out.
"""

from unittest.mock import AsyncMock

import pytest

import src.infrastructure.connectors.discogs.connector as connector_mod
from src.infrastructure.connectors.discogs.connector import DiscogsConnector
from src.infrastructure.connectors.discogs.models import (
    DiscogsCollectionPage,
    DiscogsIdentity,
)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = AsyncMock()
    monkeypatch.setattr(connector_mod, "DiscogsAPIClient", lambda: client)
    return client


class TestCollectionPageData:
    async def test_dumps_validated_page_to_plain_json(
        self, fake_client: AsyncMock
    ) -> None:
        fake_client.get_collection_page.return_value = (
            DiscogsCollectionPage.model_validate({
                "pagination": {
                    "page": 1,
                    "pages": 1,
                    "per_page": 5,
                    "items": 1,
                    "urls": {},
                },
                "releases": [
                    {
                        "id": 249504,
                        "instance_id": 1,
                        "date_added": "2026-08-01T10:00:00-07:00",
                        "basic_information": {
                            "id": 249504,
                            "title": "Rio",
                            "year": 1982,
                            "artists": [{"name": "Duran Duran"}],
                            "labels": [{"name": "EMI", "catno": "EMC 3411"}],
                            "formats": [
                                {
                                    "name": "Vinyl",
                                    "qty": "1",
                                    "descriptions": ["LP", "Album"],
                                }
                            ],
                        },
                    }
                ],
            })
        )
        connector = DiscogsConnector()

        data = await connector.get_collection_page_data("attritus", per_page=5)

        fake_client.get_collection_page.assert_awaited_once_with("attritus", 1, 5)
        assert data is not None
        pagination = data["pagination"]
        assert isinstance(pagination, dict)
        assert pagination["items"] == 1
        releases = data["releases"]
        assert isinstance(releases, list)
        basic = releases[0]["basic_information"]
        assert basic["title"] == "Rio"
        assert basic["artists"] == [{"name": "Duran Duran", "anv": "", "join": ""}]
        assert basic["formats"][0]["descriptions"] == ["LP", "Album"]

    async def test_suppressed_fetch_returns_none(self, fake_client: AsyncMock) -> None:
        fake_client.get_collection_page.return_value = None
        connector = DiscogsConnector()

        assert await connector.get_collection_page_data("attritus") is None


class TestFetchUsername:
    """Live-identity fallback for a stored token missing ``account_name``."""

    async def test_returns_username_and_backfills_account_name(
        self, fake_client: AsyncMock
    ) -> None:
        fake_client.get_identity.return_value = DiscogsIdentity(id=1, username="wash")
        connector = DiscogsConnector()

        assert await connector.fetch_username() == "wash"
        fake_client.save_account_name.assert_awaited_once_with("wash")

    async def test_backfill_failure_never_fails_the_read(
        self, fake_client: AsyncMock
    ) -> None:
        fake_client.get_identity.return_value = DiscogsIdentity(id=1, username="wash")
        fake_client.save_account_name.side_effect = RuntimeError("db down")
        connector = DiscogsConnector()

        assert await connector.fetch_username() == "wash"

    async def test_unreachable_identity_returns_none(
        self, fake_client: AsyncMock
    ) -> None:
        fake_client.get_identity.return_value = None
        connector = DiscogsConnector()

        assert await connector.fetch_username() is None
        fake_client.save_account_name.assert_not_awaited()
