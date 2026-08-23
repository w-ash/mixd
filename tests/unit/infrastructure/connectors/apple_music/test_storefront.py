"""Unit tests for Apple Music storefront resolution (v0.11.x).

The stored token's ``extra_data["storefront"]`` is the fast path; a live
``GET /v1/me/storefront`` is the fallback for tokens stored before the
connect-time lookup succeeded. A successful fallback writes the value back
onto the stored token (best-effort) so the next resolution takes the fast
path — a storage failure must never fail the resolution itself.
"""

from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.connectors.apple_music.models import AppleMusicStorefront
from src.infrastructure.connectors.apple_music.storefront import resolve_storefront

_USER = "user-1"


def _storage(stored: dict | None) -> MagicMock:
    storage = MagicMock()
    storage.load_token = AsyncMock(return_value=stored)
    storage.save_token = AsyncMock()
    storage.update_extra_data = AsyncMock()
    return storage


def _client(storefront_id: str | None = "us") -> MagicMock:
    client = MagicMock()
    client.get_storefront = AsyncMock(
        return_value=AppleMusicStorefront(id=storefront_id) if storefront_id else None
    )
    return client


class TestStoredFastPath:
    async def test_stored_storefront_skips_the_live_lookup(self):
        client = _client()
        storage = _storage({"access_token": "mut", "extra_data": {"storefront": "gb"}})

        result = await resolve_storefront(client, storage=storage, user_id=_USER)

        assert result == "gb"
        client.get_storefront.assert_not_awaited()
        storage.update_extra_data.assert_not_awaited()


class TestFallbackPersistence:
    async def test_fallback_success_writes_storefront_back_to_the_token(self):
        client = _client("us")
        storage = _storage({"access_token": "mut", "extra_data": {"foo": "bar"}})

        result = await resolve_storefront(client, storage=storage, user_id=_USER)

        assert result == "us"
        storage.update_extra_data.assert_awaited_once_with(
            "apple_music", _USER, {"storefront": "us"}
        )
        # The narrow write never rewrites token columns from a stale load.
        storage.save_token.assert_not_awaited()

    async def test_storage_write_failure_still_resolves(self):
        """The write-back is best-effort — a storage failure logs and moves on."""
        client = _client("us")
        storage = _storage({"access_token": "mut"})
        storage.update_extra_data = AsyncMock(side_effect=RuntimeError("db down"))

        result = await resolve_storefront(client, storage=storage, user_id=_USER)

        assert result == "us"

    async def test_no_stored_token_skips_the_write_back(self):
        """Nothing to write onto — the live value is still returned."""
        client = _client("us")
        storage = _storage(None)

        result = await resolve_storefront(client, storage=storage, user_id=_USER)

        assert result == "us"
        storage.update_extra_data.assert_not_awaited()
