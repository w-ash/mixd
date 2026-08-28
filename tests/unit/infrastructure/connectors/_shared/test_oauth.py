"""Shared OAuth helpers: expiry buffer, dead-grant delete, refresh carry-forward.

The provider-agnostic pieces the Spotify and Tidal token managers both run
through: the buffered expiry check, the ``invalid_grant`` compare-and-delete
(which must never destroy a newer grant), and the refresh carry-forward merge
(providers omit fields they consider unchanged; extra_data/account_name are
mixd's own and never in a provider response).
"""

import time
from unittest.mock import AsyncMock

from src.infrastructure.connectors._shared.oauth import (
    carry_forward_token_fields,
    delete_grant_if_unchanged,
    token_expired,
)
from src.infrastructure.connectors._shared.token_storage import StoredToken

_UID = "user-1"
_SERVICE = "spotify"


class TestTokenExpired:
    def test_token_with_ample_validity_is_not_expired(self) -> None:
        token = StoredToken(expires_at=int(time.time()) + 3600)
        assert not token_expired(token)

    def test_token_past_expiry_is_expired(self) -> None:
        token = StoredToken(expires_at=int(time.time()) - 60)
        assert token_expired(token)

    def test_token_inside_the_300s_buffer_reads_as_expired(self) -> None:
        # 100s of validity left is inside the default 300s pre-expiry buffer.
        token = StoredToken(expires_at=int(time.time()) + 100)
        assert token_expired(token)

    def test_custom_buffer_is_honored(self) -> None:
        token = StoredToken(expires_at=int(time.time()) + 100)
        assert not token_expired(token, buffer_seconds=0)

    def test_token_without_expires_at_reads_as_expired(self) -> None:
        assert token_expired(StoredToken(access_token="at"))


class TestDeleteGrantIfUnchanged:
    def _storage(self, stored: StoredToken | None) -> AsyncMock:
        storage = AsyncMock()
        storage.load_token = AsyncMock(return_value=stored)
        storage.delete_token = AsyncMock()
        return storage

    async def test_matching_refresh_token_is_deleted(self) -> None:
        storage = self._storage(StoredToken(refresh_token="rt-dead"))

        await delete_grant_if_unchanged(storage, _SERVICE, _UID, "rt-dead")

        storage.load_token.assert_awaited_once_with(_SERVICE, _UID)
        storage.delete_token.assert_awaited_once_with(_SERVICE, _UID)

    async def test_rotated_stored_token_is_spared(self) -> None:
        """A stale manager must not destroy a NEWER grant that landed since."""
        storage = self._storage(StoredToken(refresh_token="rt-newer"))

        await delete_grant_if_unchanged(storage, _SERVICE, _UID, "rt-dead")

        storage.delete_token.assert_not_awaited()

    async def test_no_stored_token_deletes_nothing(self) -> None:
        storage = self._storage(None)

        await delete_grant_if_unchanged(storage, _SERVICE, _UID, "rt-dead")

        storage.delete_token.assert_not_awaited()


class TestCarryForwardTokenFields:
    def _previous(self) -> StoredToken:
        return StoredToken(
            access_token="at-old",
            refresh_token="rt-old",
            scope="collection.read",
            extra_data={"authorized_at": 1_700_000_000},
            account_name="Wash",
        )

    def test_omitted_fields_are_carried_forward(self) -> None:
        merged = carry_forward_token_fields(
            StoredToken(access_token="at-new"), self._previous()
        )

        assert merged["access_token"] == "at-new"
        assert merged["refresh_token"] == "rt-old"
        assert merged["scope"] == "collection.read"
        assert merged["extra_data"] == {"authorized_at": 1_700_000_000}
        assert merged["account_name"] == "Wash"

    def test_returned_refresh_token_and_scope_win(self) -> None:
        # A genuinely rotated pair / changed grant must not be masked.
        merged = carry_forward_token_fields(
            StoredToken(
                access_token="at-new", refresh_token="rt-new", scope="new.scope"
            ),
            self._previous(),
        )

        assert merged["refresh_token"] == "rt-new"
        assert merged["scope"] == "new.scope"

    def test_no_previous_token_leaves_new_unchanged(self) -> None:
        new = StoredToken(access_token="at-new")

        merged = carry_forward_token_fields(new, None)

        assert merged == StoredToken(access_token="at-new")

    def test_previous_without_the_fields_adds_nothing(self) -> None:
        merged = carry_forward_token_fields(
            StoredToken(access_token="at-new"), StoredToken(access_token="at-old")
        )

        assert "refresh_token" not in merged
        assert "scope" not in merged
        assert "extra_data" not in merged
        assert "account_name" not in merged
