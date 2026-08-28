"""Unit tests for the shared stored-token status primitive.

``stored_token_status`` is the one probe shape shared by connectors whose
status reads only the stored token (Apple Music, Discogs, Tidal): load the
token, apply a ``ReauthRule``, render an optional cached-count ``detail``.
Each service's own probe wiring (rule + detail key) is tested beside the
service in ``tests/unit/infrastructure/connectors/<service>/test_status.py``.
"""

import time
from unittest.mock import AsyncMock

from src.infrastructure.connectors._shared.connector_status import stored_token_status
from src.infrastructure.connectors._shared.token_storage import StoredToken


def make_storage(token: StoredToken | None) -> AsyncMock:
    storage = AsyncMock()
    storage.load_token = AsyncMock(return_value=token)
    return storage


def _token(
    *,
    expires_at: int | None = None,
    refresh_token: str | None = None,
    account_name: str | None = None,
    extra_data: dict[str, object] | None = None,
) -> StoredToken:
    token = StoredToken(access_token="stored-token")
    if expires_at is not None:
        token["expires_at"] = expires_at
    if refresh_token is not None:
        token["refresh_token"] = refresh_token
    if account_name is not None:
        token["account_name"] = account_name
    if extra_data is not None:
        token["extra_data"] = extra_data
    return token


class TestStoredTokenStatus:
    async def test_no_token_is_disconnected(self) -> None:
        status = await stored_token_status("svc", "token", "u1", make_storage(None))

        assert status.name == "svc"
        assert status.auth_method == "token"
        assert status.connected is False
        assert status.auth_error is None
        assert status.detail is None

    async def test_token_present_reads_identity_and_expiry(self) -> None:
        expires = int(time.time()) + 3600
        token = _token(expires_at=expires, account_name="wash")
        status = await stored_token_status("svc", "oauth", "u1", make_storage(token))

        assert status.connected is True
        assert status.account_name == "wash"
        assert status.token_expires_at == expires
        assert status.auth_error is None

    async def test_token_without_expiry_reports_none(self) -> None:
        status = await stored_token_status("svc", "token", "u1", make_storage(_token()))

        assert status.connected is True
        assert status.token_expires_at is None

    async def test_probe_is_storage_only(self) -> None:
        storage = make_storage(_token())
        _ = await stored_token_status("svc", "token", "u1", storage)

        storage.load_token.assert_awaited_once_with("svc", "u1")


class TestReauthRules:
    async def test_default_never_rule_ignores_expiry_and_marker(self) -> None:
        token = _token(
            expires_at=int(time.time()) - 60,
            extra_data={"reauth_required": True},
        )
        status = await stored_token_status("svc", "token", "u1", make_storage(token))

        assert status.connected is True
        assert status.auth_error is None

    async def test_marker_or_expired_flags_expiry(self) -> None:
        token = _token(expires_at=int(time.time()) - 60)
        status = await stored_token_status(
            "svc",
            "browser_bridge",
            "u1",
            make_storage(token),
            reauth_rule="marker_or_expired",
        )

        assert status.connected is True
        assert status.auth_error == "reauth_required"

    async def test_marker_or_expired_flags_marker_on_fresh_token(self) -> None:
        token = _token(
            expires_at=int(time.time()) + 3600,
            extra_data={"reauth_required": True},
        )
        status = await stored_token_status(
            "svc",
            "browser_bridge",
            "u1",
            make_storage(token),
            reauth_rule="marker_or_expired",
        )

        assert status.auth_error == "reauth_required"

    async def test_marker_or_expired_clean_on_fresh_unmarked_token(self) -> None:
        token = _token(expires_at=int(time.time()) + 3600)
        status = await stored_token_status(
            "svc",
            "browser_bridge",
            "u1",
            make_storage(token),
            reauth_rule="marker_or_expired",
        )

        assert status.auth_error is None

    async def test_expired_without_refresh_flags_only_the_dead_end(self) -> None:
        token = _token(expires_at=int(time.time()) - 60)
        status = await stored_token_status(
            "svc",
            "oauth",
            "u1",
            make_storage(token),
            reauth_rule="expired_without_refresh",
        )

        assert status.connected is True
        assert status.auth_error == "reauth_required"

    async def test_expired_beside_refresh_token_is_routine(self) -> None:
        # The refresh token renews the grant on next use — no error.
        token = _token(expires_at=int(time.time()) - 60, refresh_token="rt")
        status = await stored_token_status(
            "svc",
            "oauth",
            "u1",
            make_storage(token),
            reauth_rule="expired_without_refresh",
        )

        assert status.auth_error is None

    async def test_expired_without_refresh_ignores_stray_marker(self) -> None:
        token = _token(
            expires_at=int(time.time()) + 3600,
            extra_data={"reauth_required": True},
        )
        status = await stored_token_status(
            "svc",
            "oauth",
            "u1",
            make_storage(token),
            reauth_rule="expired_without_refresh",
        )

        assert status.auth_error is None


class TestCountDetail:
    async def test_cached_count_renders_with_thousands_separator(self) -> None:
        token = _token(extra_data={"item_count": 1204})
        status = await stored_token_status(
            "svc",
            "token",
            "u1",
            make_storage(token),
            detail_key="item_count",
            detail_noun="items",
        )

        assert status.detail == "1,204 items"

    async def test_zero_count_still_renders(self) -> None:
        # 0 is the zero-state hook, not an error.
        token = _token(extra_data={"item_count": 0})
        status = await stored_token_status(
            "svc",
            "token",
            "u1",
            make_storage(token),
            detail_key="item_count",
            detail_noun="items",
        )

        assert status.detail == "0 items"

    async def test_missing_count_yields_no_detail(self) -> None:
        status = await stored_token_status(
            "svc",
            "token",
            "u1",
            make_storage(_token(extra_data={})),
            detail_key="item_count",
            detail_noun="items",
        )

        assert status.connected is True
        assert status.detail is None

    async def test_non_integer_count_yields_no_detail(self) -> None:
        token = _token(extra_data={"item_count": "1204"})
        status = await stored_token_status(
            "svc",
            "token",
            "u1",
            make_storage(token),
            detail_key="item_count",
            detail_noun="items",
        )

        assert status.detail is None
