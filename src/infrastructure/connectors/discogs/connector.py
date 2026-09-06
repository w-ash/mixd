"""Discogs connector facade and registry entry.

Discogs connects via a BYO personal access token (``auth_method "token"``,
v0.11.1 decision): the token is validated and stored by
``discogs/token_service.py`` (shared by the PUT route and ``mixd discogs
connect``), and there is no browser redirect — ``build_auth_url`` is None.

``DiscogsConnector`` is deliberately minimal, mirroring
``AppleMusicConnector``: it holds the API client and satisfies the
registry's ``Closeable`` cleanup contract, plus the application's
``DiscogsCollectionConnector`` capability protocol (structurally — no
application import) for the v0.11.1 collection snapshot. Pages cross that
seam as boundary-validated plain JSON (``model_dump`` of the Pydantic page)
because no Discogs domain entities exist yet by design. ``capabilities``
stays empty — the existing capability literals are all canonical-data
capabilities, and the snapshot writes none (import proper is v0.13.1's job).
"""

from typing import cast

from attrs import define, field

from src.config import get_logger
from src.domain.entities.shared import JsonDict
from src.infrastructure.connectors.discogs.client import DiscogsAPIClient
from src.infrastructure.connectors.protocols import ConnectorConfig

logger = get_logger(__name__).bind(service="discogs_connector")


@define(slots=True)
class DiscogsConnector:
    """Minimal Discogs connector holding the API client.

    Exposes the client for direct use and implements ``aclose()`` so the
    registry / UoW cleanup path can release the pooled httpx2 client.
    """

    _client: DiscogsAPIClient = field(init=False, factory=DiscogsAPIClient, repr=False)

    @property
    def client(self) -> DiscogsAPIClient:
        """Access to the underlying Discogs API client."""
        return self._client

    async def get_stored_username(self) -> str | None:
        """Discogs username recorded at connect time; None when not connected."""
        return await self._client.get_stored_username()

    async def fetch_username(self) -> str | None:
        """Live ``/oauth/identity`` username, backfilled onto the stored token.

        The fallback for a token row missing ``account_name``: one identity
        request re-derives the username, and a best-effort ``save_account_name``
        heals the row so the next read is storage-only again (a save failure
        never fails the read). None when Discogs could not be reached;
        raises ``DiscogsAuthRequiredError`` when no token is stored or the
        stored token is rejected.
        """
        identity = await self._client.get_identity()
        if identity is None:
            return None
        try:
            await self._client.save_account_name(identity.username)
        except Exception:
            logger.warning("Failed to backfill the Discogs account name", exc_info=True)
        return identity.username

    async def get_collection_page_data(
        self, username: str, *, page: int = 1, per_page: int = 10
    ) -> JsonDict | None:
        """One collection page, validated at the boundary then dumped to JSON.

        None means the fetch could not complete (suppressed transport
        failure) — the caller decides how to surface that.
        """
        parsed = await self._client.get_collection_page(username, page, per_page)
        if parsed is None:
            return None
        return cast("JsonDict", parsed.model_dump(mode="json"))

    async def save_collection_count(self, count: int) -> None:
        """Refresh the cached collection count on the stored token."""
        await self._client.save_collection_count(count)

    async def aclose(self) -> None:
        """Close the underlying API client's HTTP connection pool."""
        await self._client.aclose()


def get_connector_config() -> ConnectorConfig:
    """Discogs connector configuration."""
    from src.infrastructure.connectors.discogs.status import get_discogs_status
    from src.infrastructure.connectors.discogs.token_service import (
        validate_and_build_token,
    )

    return {
        "factory": DiscogsConnector,
        # No metrics — enrichment is out of scope this cycle.
        "metrics": {},
        "display_name": "Discogs",
        "category": "physical",
        "auth_method": "token",
        "capabilities": frozenset(),
        "status_fn": get_discogs_status,
        "build_auth_url": None,
        # BYO personal access token: proved live against the identity endpoint
        # before it is stored, which is what opens the generic token route.
        "validate_token": validate_and_build_token,
    }
