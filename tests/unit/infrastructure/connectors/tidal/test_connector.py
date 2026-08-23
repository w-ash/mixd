"""Unit tests for the ``TidalConnector`` facade (T5: it gains the client).

The facade holds the ``TidalAPIClient`` and satisfies the registry's
``Closeable`` cleanup contract — ``aclose()`` must delegate to the client so
the UoW cleanup path releases the pooled httpx2 client.
"""

from unittest.mock import AsyncMock

import pytest

import src.infrastructure.connectors.tidal.connector as connector_mod
from src.infrastructure.connectors.tidal.connector import TidalConnector


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    # The attrs `factory=TidalAPIClient` binds the class object directly, so
    # the seam is the class's construction: __new__ returns the fake (which,
    # not being a TidalAPIClient instance, also skips __init__).
    client = AsyncMock()
    monkeypatch.setattr(
        connector_mod.TidalAPIClient, "__new__", lambda cls, *a, **kw: client
    )
    return client


class TestFacade:
    def test_facade_exposes_the_client(self, fake_client: AsyncMock):
        connector = TidalConnector()

        assert connector.client is fake_client

    async def test_aclose_delegates_to_the_client(self, fake_client: AsyncMock):
        connector = TidalConnector()

        await connector.aclose()

        fake_client.aclose.assert_awaited_once_with()


class TestFavoritesSeam:
    """T10: the facade satisfies ``TidalFavoritesConnector`` structurally —
    pages and track details cross the seam as plain JSON data (no wire
    models escape the connector package)."""

    async def test_items_page_dumped_to_plain_data(self, fake_client: AsyncMock):
        from datetime import UTC, datetime

        from src.infrastructure.connectors.tidal.client import (
            TidalCollectionItemsPage,
        )
        from src.infrastructure.connectors.tidal.oas_models import (
            TidalCollectionItemMeta,
            TidalCollectionItemRef,
        )

        fake_client.get_collection_track_items.return_value = TidalCollectionItemsPage(
            items=[
                TidalCollectionItemRef(
                    id="t1",
                    type="tracks",
                    meta=TidalCollectionItemMeta(
                        added_at=datetime(2026, 8, 1, 12, 34, 56, tzinfo=UTC)
                    ),
                ),
                TidalCollectionItemRef(id="t2", type="tracks", meta=None),
            ],
            next_cursor="c2",
            total=1204,
        )
        connector = TidalConnector()

        page = await connector.get_collection_items_page(None)

        assert page == {
            "items": [
                {"id": "t1", "added_at": "2026-08-01T12:34:56+00:00"},
                {"id": "t2", "added_at": None},
            ],
            "next_cursor": "c2",
            "total": 1204,
        }
        fake_client.get_collection_track_items.assert_awaited_once_with(None)

    async def test_items_page_none_passes_through(self, fake_client: AsyncMock):
        fake_client.get_collection_track_items.return_value = None
        connector = TidalConnector()

        assert await connector.get_collection_items_page("c9") is None
        fake_client.get_collection_track_items.assert_awaited_once_with("c9")

    async def test_track_display_data_assembles_title_and_artists(
        self, fake_client: AsyncMock
    ):
        from src.infrastructure.connectors.tidal.oas_models import (
            JsonApiDocument,
            TidalArtistAttributes,
            TidalArtistResource,
            TidalTrackAttributes,
            TidalTrackResource,
        )

        fake_client.get_track.return_value = JsonApiDocument[TidalTrackResource](
            data=TidalTrackResource(
                id="t1",
                type="tracks",
                attributes=TidalTrackAttributes(title="Rio", duration="PT2M58S"),
            ),
            included=[
                TidalArtistResource(
                    id="a1",
                    type="artists",
                    attributes=TidalArtistAttributes(name="Duran Duran"),
                )
            ],
        )
        connector = TidalConnector()

        display = await connector.get_track_display_data("t1")

        assert display == {"title": "Rio", "artists": ["Duran Duran"]}
        # The snapshot needs no successor pointer — keep the include minimal.
        _args, kwargs = fake_client.get_track.await_args
        assert kwargs.get("include_replacement") is False

    async def test_track_display_data_none_for_missing_track(
        self, fake_client: AsyncMock
    ):
        fake_client.get_track.return_value = None
        connector = TidalConnector()

        assert await connector.get_track_display_data("gone") is None

    async def test_save_favorites_count_delegates(self, fake_client: AsyncMock):
        connector = TidalConnector()

        await connector.save_favorites_count(42)

        fake_client.save_favorites_count.assert_awaited_once_with(42)
