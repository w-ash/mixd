"""Tidal connector facade and registry entry.

Registers Tidal with the connector discovery registry: browser OAuth
(``auth_method "oauth"``, PKCE public client — ``tidal/auth.py``), the
storage-only status probe, and a minimal facade mirroring
``DiscogsConnector``: it holds the JSON:API client (``TidalAPIClient``,
v0.11.3 T5) and satisfies the registry's cleanup contract — constructible
from the ``factory`` callable with no arguments (the contract tests
instantiate every registered connector) and closeable via ``aclose()``
(the UoW cleanup path calls it on every cached connector to release the
pooled httpx2 client).

For the v0.11.3 favorites snapshot the facade also satisfies the
application's ``TidalFavoritesConnector`` capability protocol (structurally
— no application import): pages and track details cross that seam as plain
JSON data because no Tidal domain entities exist yet by design (matching
and import are v0.13.x's job), the same posture as ``DiscogsConnector``.
``capabilities`` stays empty — the existing capability literals are all
canonical-data capabilities, and the snapshot writes none.
"""

from attrs import define, field

from src.domain.entities.shared import JsonDict, JsonValue
from src.infrastructure.connectors.protocols import ConnectorConfig
from src.infrastructure.connectors.tidal.client import (
    TIDAL_COUNTRY_CODE,
    TidalAPIClient,
)
from src.infrastructure.connectors.tidal.models import (
    collection_item_from_ref,
    tidal_track_detail_from_document,
)


@define(slots=True)
class TidalConnector:
    """Minimal Tidal connector holding the API client."""

    _client: TidalAPIClient = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        self._client = TidalAPIClient()

    @property
    def client(self) -> TidalAPIClient:
        """Access to the underlying Tidal API client."""
        return self._client

    async def get_collection_items_page(
        self, cursor: str | None = None
    ) -> JsonDict | None:
        """One favorites page, validated at the boundary then dumped to plain data.

        ``{"items": [{"id", "added_at"}], "next_cursor", "total"}`` —
        ``added_at`` an ISO-8601 string or None, ``total`` the collection
        size when Tidal serves ``meta.total`` (None otherwise), and
        ``next_cursor`` None on the last page. Returns ``None`` when the
        fetch could not complete (suppressed transport failure) — the
        caller decides how to surface that.
        """
        page = await self._client.get_collection_track_items(cursor)
        if page is None:
            return None
        items: list[JsonValue] = [
            {
                "id": item.track_id,
                "added_at": (
                    item.added_at.isoformat() if item.added_at is not None else None
                ),
            }
            for item in map(collection_item_from_ref, page.items)
        ]
        return {
            "items": items,
            "next_cursor": page.next_cursor,
            "total": page.total,
        }

    async def get_track_display_data(self, track_id: str) -> JsonDict | None:
        """Title + ordered artist names for one track, as plain data.

        The favorites relationship carries only identifiers and ``addedAt``,
        so display rows need this per-track lookup. The successor include is
        skipped — the snapshot shows the favorite as-is. ``None`` when the
        track cannot be fetched (404, suppressed transport failure) or the
        document carries no track.
        """
        document = await self._client.get_track(
            track_id, TIDAL_COUNTRY_CODE, include_replacement=False
        )
        if document is None:
            return None
        detail = tidal_track_detail_from_document(document)
        if detail is None:
            return None
        artists: list[JsonValue] = list(detail.artist_names)
        return {"title": detail.track.title, "artists": artists}

    async def save_favorites_count(self, count: int) -> None:
        """Refresh the cached favorites count on the stored token."""
        await self._client.save_favorites_count(count)

    async def aclose(self) -> None:
        """Close the client's pooled httpx2 resources."""
        await self._client.aclose()


def get_connector_config() -> ConnectorConfig:
    """Tidal connector configuration."""
    from src.infrastructure.connectors._shared.connector_status import (
        get_tidal_status,
    )
    from src.infrastructure.connectors.tidal.auth import build_auth_url

    return {
        "dependencies": [],
        "factory": lambda _params: TidalConnector(),
        # No register_metrics — enrichment is out of scope this cycle.
        "metrics": {},
        "display_name": "TIDAL",
        "category": "streaming",
        "auth_method": "oauth",
        # The favorites snapshot writes no canonical data; the existing
        # capability literals are all canonical-data capabilities, so the
        # set stays empty until a later epic adds one.
        "capabilities": frozenset(),
        "status_fn": get_tidal_status,
        "build_auth_url": build_auth_url,
    }
